#!/usr/bin/env python3
"""Export the Decode comparison from the CP4 + multi-LoRA teaching article."""

import argparse
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re
from xml.etree import ElementTree as ET


# Reviewed article script containing only mountSlotGuide and mountGpuCards.
CONTROL_SCRIPT_SHA256 = "6882dad65ff0f4f7e0e338144792f50d94dbc26dc5d28d999941572d0a4ce758"
TAGS = set("""
    a aside b br button caption code dd details div dl dt figcaption figure
    h2 h3 h4 h5 i label li mark ol option p section select small span strong sub
    summary sup table tbody td th thead tr
""".split())
ATTRS = set("""
    aria-hidden aria-label aria-labelledby aria-live aria-pressed class colspan
    data-change data-gpu-card data-kernel-role data-label data-pair-side
    data-page data-phase data-row data-slot-cell data-slot-controls data-slot-frame
    data-slot-message data-slot-route data-slot-saved-bytes data-slot-writes
    data-step hidden id name open role scope start type value
""".split())


class ArticleParser(HTMLParser):
    """Find the heading's nearest section and collect candidate styles and scripts."""

    def __init__(self, source):
        super().__init__()
        self.lines = [0] + [m.end() for m in re.finditer("\n", source)]
        self.sections = []
        self.start = self.end = None
        self.in_head = False
        self.in_noscript = False
        self.raw = None
        self.styles = []
        self.scripts = []
        self.feed(source)
        self.close()
        if self.end is None:
            raise ValueError("missing or incomplete section containing #decode-comparison")

    def source_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "head":
            self.in_head = True
        elif tag == "noscript":
            self.in_noscript = True
        elif tag == "section":
            self.sections.append(self.source_offset())
        if values.get("id") == "decode-comparison":
            if self.start is not None or not self.sections:
                raise ValueError("expected a single #decode-comparison inside a section")
            self.start = self.sections[-1]
        elif tag == "style" and self.in_head and not self.in_noscript:
            self.raw = self.styles
            self.raw.append("")
        elif tag == "script" and not values:
            self.raw = self.scripts
            self.raw.append("")

    def handle_data(self, data):
        if self.raw is not None:
            self.raw[-1] += data

    def handle_endtag(self, tag):
        if tag == "section" and self.sections and self.sections.pop() == self.start:
            self.end = self.source_offset()
        elif tag in {"style", "script"}:
            self.raw = None
        elif tag == "head":
            self.in_head = False
        elif tag == "noscript":
            self.in_noscript = False


class SectionParser(HTMLParser):
    """Copy the article's semantic elements and controls, with source links as text."""

    def __init__(self, fragment):
        super().__init__()
        self.tree = ET.TreeBuilder()
        self.stack = []
        self.feed(fragment)
        self.close()
        if self.stack:
            raise ValueError("incomplete comparison markup")
        self.section = self.tree.close()

    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:
            raise ValueError(f"review unexpected comparison element <{tag}> before export")
        values = dict(attrs)
        if not self.stack:
            values.pop("hidden", None)
        if tag == "a":
            values.pop("href", None)
        if set(values) - ATTRS:
            raise ValueError(f"review unexpected attributes on <{tag}> before export")
        self.tree.start("span" if tag == "a" else tag, {k: v or "" for k, v in values.items()})
        if tag == "br":
            self.tree.end(tag)
        else:
            self.stack.append(tag)

    def handle_data(self, data):
        self.tree.data(data)

    def handle_endtag(self, tag):
        if tag != "br":
            if not self.stack or self.stack.pop() != tag:
                raise ValueError(f"unbalanced comparison markup at </{tag}>")
            self.tree.end("span" if tag == "a" else tag)


STAGES = ("draft", "input", "qkv", "attention", "output", "expert", "down", "head", "accept")
SHARED = {"draft", "attention", "accept"}


def check_section(section):
    ids = [el.get("id") for el in section.iter() if "id" in el.attrib]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate comparison anchors")
    stage_ids = [f"compare-{phase}" for phase in STAGES]
    stages = [el for el in section.iter() if el.get("id") in stage_ids]
    if [el.get("id") for el in stages] != stage_ids:
        raise ValueError("expected the nine stages in forward order")
    compact = all(el.tag == "details" and "decode-stage" in el.get("class", "").split() for el in stages)
    if compact:
        opened = [el.get("data-phase") for el in stages if "open" in el.attrib]
        if opened != ["qkv"] or any(el.get("name") != "decode-stage" for el in stages):
            raise ValueError("expected a single open Q/KV panel in the named stage group")
    for phase, stage in zip(STAGES, stages):
        if phase not in SHARED:
            rows = [el for el in stage.iter() if "operation-row" in el.get("class", "").split()]
            if compact and not rows:
                raise ValueError(f"missing aligned operations at {phase}")
            for row in rows or [stage]:
                sides = [el.get("data-pair-side") for el in row.iter() if "data-pair-side" in el.attrib]
                if set(sides) != {"before", "after"} or (compact and sides != ["before", "after"]):
                    raise ValueError(f"missing before/after comparison at {phase}")
        calls = [el for el in stage.iter("details")
                 if "kernel-drill" in el.get("class", "").split()
                 and "kernel-chain" not in el.get("class", "").split()]
        if not calls:
            raise ValueError(f"missing expandable kernel inventory at {phase}")
        for call in calls:
            fields = call.findall("dl/div/dd")
            if (call.find("summary") is None or len(fields) != 5
                    or any(not "".join(field.itertext()).strip() for field in fields)):
                raise ValueError(f"incomplete kernel input, calculation, grid, output or traffic at {phase}")
    return ids


def css_blocks(css):
    """Read the source stylesheet's ordinary rules and nested media blocks."""
    start = depth = 0
    # Quoted CSS content is opaque, including braces inside a string.
    for token in re.finditer(r"""'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|[{}]""", css):
        if token[0] == "{":
            if depth == 0:
                selector = css[start:token.start()].strip()
                body = token.end()
            depth += 1
        elif token[0] == "}":
            depth -= 1
            if depth < 0:
                raise ValueError("unexpected CSS closing brace")
            if depth == 0:
                yield selector, css[body:token.start()].strip()
                start = token.end()
    if depth or css[start:].strip():
        raise ValueError("unsupported or incomplete CSS rule")


def comparison_styles(styles, section, ids):
    classes = {name for el in section.iter() for name in el.get("class", "").split()} | {"page"}
    tags = {el.tag for el in section.iter()} | {"body", "html"}
    attributes = {name for el in section.iter() for name in el.attrib} | {"open"}
    names = set(ids)

    def relevant(selector):
        # Split selector lists only outside functional pseudo-classes such as :is().
        if ".explain-slide[hidden]" in selector:
            return False
        if any(a not in attributes for a in re.findall(r"\[([\w-]+)", selector)):
            return False
        if any(c not in classes for c in re.findall(r"\.([\w-]+)", selector)):
            return False
        if any(i not in names for i in re.findall(r"#([\w-]+)", selector)):
            return False
        return all(t in tags for t in re.findall(r"(?:^|[ >+~])([a-z][\w-]*)", selector))

    def filter_rules(css):
        rules = []
        for selector, body in css_blocks(css):
            if selector.startswith("@media"):
                body = filter_rules(body)
                if body:
                    rules.append(f"{selector} {{\n{body}\n}}")
            elif selector.startswith("@"):
                raise ValueError("review stylesheet at-rule before export")
            else:
                # All functional selector lists in the source are single-level.
                parts = re.split(r",(?![^()]*\))", selector)
                kept = [s.strip() for s in parts if relevant(s.strip())]
                # This desktop offset reserved space for the original deck's left sidebar.
                if kept and not (kept == ["body"] and "padding-left:244px" in body.replace(" ", "")):
                    rules.append(f"{', '.join(kept)} {{ {body} }}")
        return "\n".join(rules)

    css = filter_rules(re.sub(r"/\*.*?\*/", "", "\n".join(styles), flags=re.S))
    if not all(s in css for s in (":root", ".page", ".kernel-drill")):
        raise ValueError("missing theme or comparison styles")
    if re.search(r"@import|url\s*\(|</style", css, re.I):
        raise ValueError("review external or unsupported CSS resource before export")
    return "\n".join(line.rstrip() for line in css.splitlines())


def export(source):
    article = ArticleParser(source)
    section = SectionParser(source[article.start:article.end] + "</section>").section
    heading = section.find(".//h2[@id='decode-comparison']")
    if heading is None:
        raise ValueError("missing #decode-comparison heading")
    ids = check_section(section)
    css = comparison_styles(article.styles, section, ids)
    scripts = [s for s in article.scripts if sha256(s.encode()).hexdigest() == CONTROL_SCRIPT_SHA256]
    controls = any(section.find(selector) is not None for selector in
                   (".//button[@data-slot-frame]", ".//select[@id='gpu-sample-card']"))
    script = ""
    if controls:
        if len(scripts) != 1:
            raise ValueError("GPU/slot control script changed: review it before updating CONTROL_SCRIPT_SHA256")
        script = f"<script>{scripts[0]}</script>"
    markup = ET.tostring(section, encoding="unicode", method="html")
    if re.search(r"(?:https?|ftp|file)://|(?:/home/|/Users/|/mnt/)|\b(?:\d{1,3}\.){3}\d{1,3}\b", markup, re.I):
        raise ValueError("review URL, filesystem path or network address in comparison content")
    title = escape("".join(heading.itertext()))
    return f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{title}</title>
<style>
{css}
</style></head><body>
<main class="page">{markup}</main>
{script}
</body></html>
"""


def preview(html_path, png_path):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ValueError("--preview requires Python Playwright and its Chromium browser") from exc
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900}, device_scale_factor=1,
                                locale="zh-CN", offline=True)
        page.goto(html_path.resolve().as_uri())
        page.evaluate("document.fonts.ready")
        stage = page.locator('#compare-qkv .stage-comparison')
        if not stage.count():
            stage = page.locator('.overview-stage[data-phase="qkv"]')
        image = stage.screenshot(type="png", animations="disabled")
        browser.close()
    png_path.write_bytes(image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="original complete article HTML")
    parser.add_argument("output", type=Path, help="standalone comparison HTML")
    parser.add_argument("--preview", type=Path, metavar="PNG", help="also capture the Q/KV stage with Chromium")
    args = parser.parse_args()
    paths = [path.resolve() for path in (args.input, args.output, args.preview) if path]
    if len(set(paths)) != len(paths):
        parser.error("input, HTML output and PNG output must be different paths")
    try:
        document = export(args.input.read_text(encoding="utf-8"))
        args.output.write_bytes(document.encode("utf-8"))
        if args.preview:
            preview(args.output, args.preview)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    main()
