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
    h2 h3 h4 h5 i label li mark ol option p pre section select small span strong sub
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
CARD_ROLES = ("card-input", "card-program", "card-output", "card-grid", "card-traffic")


# Stable IDs in the public HTML format. Validate older articles against their own format markers.
CALL_KINDS = {"kind-" + name for name in (
    "kernel", "torch", "library", "view", "copy", "composite", "collective", "example",
)}


def check_call_kind(call, summary, grid, where):
    badges = [el for el in summary.iter("span") if "entry-kind" in el.get("class", "").split()]
    if len(badges) != 1 or not "".join(badges[0].itertext()).strip():
        raise ValueError(f"missing or ambiguous call type in summary at {where}")
    badge_kinds = {c for c in badges[0].get("class", "").split() if c.startswith("kind-")}
    body_kinds = {c for c in call.find("dl").get("class", "").split() if c.startswith("kind-")}
    if len(badge_kinds) != 1 or not badge_kinds <= CALL_KINDS or badge_kinds != body_kinds:
        raise ValueError(f"unknown or inconsistent call type at {where}")
    boundaries = [el for el in grid.findall("span") if "dispatch-boundary" in el.get("class", "").split()]
    if (len(boundaries) != 1 or boundaries[0] is not grid[0]
            or (grid.text or "").strip() or not "".join(boundaries[0].itertext()).strip()):
        raise ValueError(f"expected dispatch boundary first in CTA field at {where}")


def with_class(nodes, name):
    return [el for el in nodes if name in el.get("class", "").split()]


def check_backend_fields(element, where):
    for role in ("backend-name", "backend-implementation"):
        matches = with_class(element, role)
        if len(matches) != 1 or not "".join(matches[0].itertext()).strip():
            raise ValueError(f"missing or ambiguous {role} at {where}")


def check_stage_backends(stage, phase, collapsed):
    briefs = with_class(stage.iter(), "stage-brief")
    if len(briefs) != 1:
        raise ValueError(f"expected one stage summary for backend descriptions at {phase}")
    sides = ("brief-common",) if phase in SHARED else ("brief-before", "brief-after")
    for side in sides:
        columns = with_class(briefs[0].iter(), side)
        entries = [entry for column in columns for entry in with_class(column.iter(), "stage-backends")]
        if len(entries) != 1 or entries[0] in collapsed:
            raise ValueError(f"expected one visible backend description at {phase}/{side}")
        check_backend_fields(entries[0], f"{phase}/{side}")


def check_kernel_card(call, phase, has_contracts, uses_template, has_input_rows, has_call_kinds,
                      has_backends):
    summary = call.find("summary")
    name = "".join(summary.itertext()).strip() if summary is not None else ""
    where = f"{phase}: {name or '(unnamed call)'}"
    fields = call.findall("dl/div/dd")
    if not name or len(fields) != 5 or any(not "".join(field.itertext()).strip() for field in fields):
        raise ValueError(f"incomplete kernel input, calculation, grid, output or traffic at {where}")
    if uses_template:
        bodies = call.findall("dl")
        if len(bodies) != 1 or "kernel-card" not in bodies[0].get("class", "").split():
            raise ValueError(f"missing shared kernel card at {where}")
        rows = list(bodies[0])
        if (len(rows) != len(CARD_ROLES)
                or any(row.tag != "div" or [child.tag for child in row] != ["dt", "dd"]
                       or set(row.get("class", "").split()).intersection(CARD_ROLES) != {role}
                       for row, role in zip(rows, CARD_ROLES))):
            raise ValueError(f"missing or out-of-order kernel card field at {where}")
        for field in (fields[0], fields[2]):
            extent = field.find(".//div[@class='tensor-size']")
            if extent is None or not "".join(extent.itertext()).strip():
                raise ValueError(f"missing Decode input or output size at {where}")
    if has_backends:
        entries = with_class(fields[1], "call-backend")
        children = list(fields[1])
        program = fields[1].find("pre[@class='kernel-pseudocode']")
        if (len(entries) != 1 or program is None
                or children.index(entries[0]) + 1 != children.index(program)):
            raise ValueError(f"expected backend immediately before pseudocode at {where}")
        check_backend_fields(entries[0], where)
    if has_call_kinds:
        check_call_kind(call, summary, fields[3], where)
    if has_input_rows:
        rows = fields[0].findall("table[@class='input-variables']/tbody/tr")
        for row in rows:
            parts = (row.find("th[@scope='row']"), row.find("td/span[@class='variable-example']"),
                     row.find("td/span[@class='variable-reason']"))
            if any(part is None or not "".join(part.itertext()).strip() for part in parts):
                raise ValueError(f"missing variable, example or reason at {where}")
        if not rows or fields[0].find("code") is not None or fields[0].find("p") is not None:
            raise ValueError(f"expected a variable table as the Input at {where}")
    if has_contracts or uses_template:
        labels = [(el.text or "").strip() for el in call.findall("dl/div/dt")]
        signatures = [fields[i].find("code[@class='tensor-signature']") for i in ((2,) if has_input_rows else (0, 2))]
        program = fields[1].find("pre[@class='kernel-pseudocode']/code")
        if (len(labels) != 5 or not labels[0].startswith("Input") or not labels[1].startswith("伪代码")
                or not labels[2].startswith("Output") or not labels[3].startswith("CTA")
                or "通信" not in labels[4]
                or not all(el is not None and (el.text or "").strip() for el in signatures)
                or program is None or sum(bool(line.strip()) for line in (program.text or "").splitlines()) < 2):
            raise ValueError(f"missing Input, pseudocode, Output, CTA or communication at {where}")


def check_section(section):
    ids = [el.get("id") for el in section.iter() if "id" in el.attrib]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate comparison anchors")
    stage_ids = [f"compare-{phase}" for phase in STAGES]
    stages = [el for el in section.iter() if el.get("id") in stage_ids]
    if [el.get("id") for el in stages] != stage_ids:
        raise ValueError("expected the nine stages in forward order")
    compact = all(el.tag == "details" and "decode-stage" in el.get("class", "").split() for el in stages)
    continuous = all(el.tag == "div" and "decode-stage" in el.get("class", "").split() for el in stages)
    classes = {name for el in section.iter() for name in el.get("class", "").split()}
    has_call_kinds = "kernel-call-kinds" in classes
    has_backends = "kernel-backends" in classes
    uses_template = has_backends or has_call_kinds or bool({"kernel-card-template", "kernel-card"} & classes)
    collapsed = set()
    if continuous:
        for panel in section.iter("details"):
            if "open" not in panel.attrib:
                for child in panel:
                    if child.tag != "summary":
                        collapsed.update(child.iter())
    if compact:
        opened = [el.get("data-phase") for el in stages if "open" in el.attrib]
        if opened != ["qkv"] or any(el.get("name") != "decode-stage" for el in stages):
            raise ValueError("expected a single open Q/KV panel in the named stage group")
    for phase, stage in zip(STAGES, stages):
        if has_backends:
            check_stage_backends(stage, phase, collapsed)
        if continuous:
            briefs = [el for el in stage.iter() if "stage-brief" in el.get("class", "").split()]
            if len(briefs) != 1 or briefs[0] in collapsed:
                raise ValueError(f"missing initially visible stage summary at {phase}")
            if phase not in SHARED:
                for side in ("brief-before", "brief-after"):
                    if not any(side in el.get("class", "").split() and el not in collapsed
                               for el in briefs[0].iter()):
                        raise ValueError(f"missing initially visible {side} summary at {phase}")
        if phase not in SHARED:
            if continuous:
                diagrams = [el for el in stage.iter("figure") if "stage-comparison" in el.get("class", "").split()]
                if len(diagrams) != 1:
                    raise ValueError(f"expected one operation diagram at {phase}")
                if phase == "qkv" and diagrams[0] in collapsed:
                    raise ValueError("the Q/KV memory diagram should be visible when the page opens")
            rows = [el for el in stage.iter() if "operation-row" in el.get("class", "").split()]
            if (compact or continuous) and not rows:
                raise ValueError(f"missing aligned operations at {phase}")
            for row in rows or [stage]:
                sides = [el.get("data-pair-side") for el in row.iter() if "data-pair-side" in el.attrib]
                if set(sides) != {"before", "after"} or ((compact or continuous) and sides != ["before", "after"]):
                    raise ValueError(f"missing before/after comparison at {phase}")
        calls = [el for el in stage.iter("details")
                 if "kernel-drill" in el.get("class", "").split()
                 and (el.find("dl") is not None or not {"kernel-chain", "call-group"} & set(el.get("class", "").split()))]
        if not calls:
            raise ValueError(f"missing expandable kernel inventory at {phase}")
        for call in calls:
            check_kernel_card(call, phase, "kernel-contracts" in classes, uses_template,
                              "kernel-input-examples" in classes, has_call_kinds, has_backends)
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
    if "kernel-card" in classes and not re.search(r"\.kernel-card(?![\w-])", css):
        raise ValueError("missing shared kernel card styles")
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
