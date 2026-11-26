#!/usr/bin/env python3
"""Export the Decode comparison from the CP4 + multi-LoRA teaching article."""

import argparse
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
from math import ceil, floor
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
    data-change data-memory-routes data-call-io data-call-details data-launch-boundaries data-launch-scope data-gpu-card data-kernel-role data-label data-pair-side
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
        self.full_source = None
        self.in_noscript = False
        self.raw = None
        self.styles = []
        self.scripts = []
        self.feed(source)
        self.close()
        if self.end is None and self.full_source is None:
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
        elif (tag == "meta" and self.in_head and not self.in_noscript
              and values.get("name") == "article-full-source"):
            if self.full_source is not None:
                raise ValueError("duplicate article-full-source marker")
            filename = values.get("content") or ""
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.html", filename):
                raise ValueError("article-full-source must name an HTML file in the same directory")
            self.full_source = filename
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


def check_execution_fields(element, where, roles=("execution-api", "execution-owner", "execution-work", "execution-evidence")):
    for role in roles:
        rows = with_class(element, role)
        if len(rows) != 1:
            raise ValueError(f"missing or ambiguous {role} at {where}")
        values = with_class(rows[0].findall("span"), "execution-value")
        if len(values) != 1 or not "".join(values[0].itertext()).strip():
            raise ValueError(f"expected a single value for {role} at {where}")


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


TRANSFER_FIELDS = ("producer", "buffer", "consumer", "write", "handoff", "next-read", "link")


def check_stage_transfers(stage, phase, collapsed):
    maps = with_class(stage.iter(), "stage-transfers")
    if len(maps) != 1 or maps[0] in collapsed:
        raise ValueError(f"expected one initially visible tensor transfer map at {phase}")
    paths = with_class(maps[0].iter(), "transfer-path")
    visible_path = False
    for index, path in enumerate(paths, start=1):
        visible_fields = set()
        for field in TRANSFER_FIELDS:
            values = with_class(path.iter(), "transfer-" + field)
            tag = "span" if field in ("producer", "buffer", "consumer") else "dd"
            payload = values[0].find(tag) if len(values) == 1 else None
            if payload is None or not "".join(payload.itertext()).strip():
                raise ValueError(f"missing or ambiguous {field} value on tensor path {index} at {phase}")
            if values[0] not in collapsed:
                visible_fields.add(field)
        if path not in collapsed and {"write", "handoff", "link"} <= visible_fields:
            visible_path = True
    if not visible_path:
        raise ValueError(f"expected an initially visible tensor path with write, handoff and link at {phase}")


def check_variable_examples(field, where):
    rows = field.findall("table[@class='input-variables']/tbody/tr")
    for row in rows:
        parts = (row.find("th[@scope='row']"), row.find("td/span[@class='variable-example']"),
                 row.find("td/span[@class='variable-reason']"))
        if any(part is None or not "".join(part.itertext()).strip() for part in parts):
            raise ValueError(f"missing variable, example or reason at {where}")
    if not rows or field.find("code") is not None or field.find("p") is not None:
        raise ValueError(f"expected a variable table as the Input at {where}")


def check_kernel_card(call, phase, has_contracts, uses_template, has_input_rows, has_call_kinds,
                      execution_format, has_transfer_map=False):
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
    if has_transfer_map:
        paths = with_class(fields[2], "output-path")
        if len(paths) != 1:
            raise ValueError(f"missing or ambiguous output path at {where}")
        for role in ("output-storage", "output-consumer", "output-handoff"):
            notes = with_class(paths[0], role)
            if len(notes) != 1 or not any((label.tail or "").strip() for label in notes[0].findall("span")):
                raise ValueError(f"missing {role} explanation at {where}")
    if execution_format:
        program_field = fields[1]
        entries = with_class(program_field, execution_format)
        children = list(program_field)
        program = program_field.find("pre[@class='kernel-pseudocode']")
        if (len(entries) != 1 or program is None
                or children.index(entries[0]) + 1 != children.index(program)):
            raise ValueError(f"expected {execution_format} immediately before pseudocode at {where}")
        if execution_format == "call-execution":
            if with_class(program_field.iter(), "call-backend"):
                raise ValueError(f"obsolete backend label beside execution details at {where}")
            check_execution_fields(entries[0], where)
        else:
            check_backend_fields(entries[0], where)
    if has_call_kinds:
        check_call_kind(call, summary, fields[3], where)
    if has_input_rows:
        check_variable_examples(fields[0], where)
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


def check_implementation_details(call, route, where):
    """Traffic accounting stays visible; the disclosure explains the implementation."""
    bodies = call.findall("dl")
    if len(bodies) != 1 or not {"kernel-card", "paired-detail"} <= set(bodies[0].get("class", "").split()):
        raise ValueError(f"expected one implementation detail card at {where}")
    body = bodies[0]
    roles = ["card-program", "card-grid", "card-input"]
    if with_class(body, "card-derivation"):
        roles.append("card-derivation")
    if len(body) != len(roles):
        raise ValueError(f"unexpected implementation sections at {where}")
    fields = []
    for row, role in zip(body, roles):
        if (row.tag != "div" or role not in row.get("class", "").split()
                or [child.tag for child in row] != ["dt", "dd"]
                or not all("".join(child.itertext()).strip() for child in row)):
            raise ValueError(f"missing {role} in implementation details at {where}")
        fields.append(row.find("dd"))
    repeated_sections = {"kernel-memory-route", "kernel-io", "execution-work", "output-path", "input-extent",
                         "card-output", "card-traffic", "tensor-size"}
    if any(repeated_sections.intersection(el.get("class", "").split()) for el in body.iter()):
        raise ValueError(f"implementation details repeat the diagram's work or traffic ledger at {where}")
    program, grid, inputs = fields[:3]
    executor = program.find("div[@class='call-execution']")
    code = program.find("pre[@class='kernel-pseudocode']")
    if executor is None or code is None or list(program) != [executor, code]:
        raise ValueError(f"expected device evidence followed by pseudocode at {where}")
    check_execution_fields(executor, where, roles=("execution-api", "execution-owner", "execution-evidence"))
    snippet = code.find("code")
    if snippet is None or sum(bool(line.strip()) for line in (snippet.text or "").splitlines()) < 2:
        raise ValueError(f"expected a traceable implementation in the pseudocode at {where}")
    check_call_kind(call, call.find("summary"), grid, where)
    check_variable_examples(inputs, where)
    if len(fields) == 4:
        # A useful derivation can repeat a symbol or total; copying a whole visible
        # data-flow field into the optional note gives the reader no explanation.
        def compact(element):
            return "".join("".join(element.itertext()).split())
        detail_nodes = set(call.iter())
        visible_facts = {compact(dd) for dd in route.iter("dd") if dd not in detail_nodes}
        if compact(fields[3]) in visible_facts:
            raise ValueError(f"derivation repeats a data flow field verbatim at {where}")


LINK_KINDS = {"memory-link-" + kind for kind in ("local", "host", "peer", "conditional", "unknown")}


def check_link_kind(link, where):
    kinds = {c for c in link.get("class", "").split() if c.startswith("memory-link-")}
    if len(kinds) != 1 or not kinds <= LINK_KINDS:
        raise ValueError(f"expected one known link classification at {where}")


SCOPE_CLASSES = {"kernel-execution-boundary", "call-implementation-scope"}
# Text that makes the machine-readable scope legible when the card is closed.
SCOPE_LABELS = {"single": "一次 GPU kernel 发射", "continuation": "续段", "multiple": "多次",
                "conditional": "运行条件", "opaque": "待确认", "view": "视图", "host": "主机"}


def check_execution_scope(scope, call, badge, where):
    """Only a single evidenced launch or a continuation earns a kernel boundary."""
    mode = scope.get("data-launch-scope")
    hardware = mode in {"single", "continuation"}
    role = "kernel-execution-boundary" if hardware else "call-implementation-scope"
    if mode not in SCOPE_LABELS or (set(scope.get("class", "").split()) & SCOPE_CLASSES) != {role}:
        raise ValueError(f"launch scope and boundary style disagree at {where}")
    if hardware and "kind-view" in badge.get("class", "").split():
        raise ValueError(f"a metadata view cannot launch a GPU kernel at {where}")
    labels = with_class(scope, "scope-heading")
    ledgers = scope.findall("dl")
    if len(labels) != 1 or len(ledgers) != 1 or list(scope) != [labels[0], ledgers[0], call]:
        raise ValueError(f"execution scope must contain its label, work and implementation details at {where}")
    title = "".join(labels[0].itertext()).strip()
    if SCOPE_LABELS[mode] not in title or (("续段" in title) != (mode == "continuation")):
        raise ValueError(f"missing or inconsistent execution scope label at {where}")
    ledger = ledgers[0]
    fields = ["scope-work", "memory-onchip", "scope-note"]
    actual = [set(row.get("class", "").split()) & set(fields) for row in ledger]
    expected = [{role} for role in fields if role == "memory-onchip" or {role} in actual]
    if (actual != expected or any([el.tag for el in row] != ["dt", "dd"]
                                  or not all("".join(el.itertext()).strip() for el in row) for row in ledger)):
        raise ValueError(f"expected one on-chip field and distinct execution facts at {where}")
    if mode == "continuation" and not with_class(ledger, "scope-note"):
        raise ValueError(f"explain which launch this continuation belongs to at {where}")
    return ledger


def check_integrated_calls(stage, phase, calls, collapsed, *, explicit=False):
    """Each call owns a visible Input and Output, even when nested inside a composite."""
    def only(nodes, role):
        matches = with_class(nodes, role)
        if len(matches) != 1:
            raise ValueError(f"expected one {role} at {where}")
        return matches[0]

    def content(el):
        return "".join(el.itertext()).strip()

    parents = {child: parent for parent in stage.iter() for child in parent}
    steps = set(with_class(stage.iter(), "kernel-step"))
    unclaimed_io = set(with_class(stage.iter(), "kernel-io"))
    unclaimed_scopes = {el for el in stage.iter()
                        if "data-launch-scope" in el.attrib or SCOPE_CLASSES.intersection(el.get("class", "").split())}
    fact_classes = {"memory-" + key for key in ("read", "write", "consumer", "handoff", "onchip", "link", "evidence")}
    if explicit:
        fact_classes.update(("scope-work", "scope-note"))
    unclaimed_facts = {el for el in stage.iter() if fact_classes.intersection(el.get("class", "").split())}
    for call in calls:
        where = phase
        scope = parents[call] if explicit else None
        step = parents.get(parents.get(scope)) if explicit else parents[call]
        if step not in steps:
            raise ValueError(f"expected one enclosing template per call at {where}")
        steps.remove(step)
        heading = only(step, "kernel-step-heading")
        io = only(step, "kernel-io")
        unclaimed_io.remove(io)
        if list(step) != ([heading, io] if explicit else [heading, io, call]):
            raise ValueError(f"expected heading, I/O and details in one template at {where}")
        summary = call.find("summary")
        names = heading.findall("code")
        if (summary is None or len(names) != 1 or not content(names[0])
                or [content(el) for el in summary.findall("code")] != [content(names[0])]):
            raise ValueError(f"visible call and implementation names differ at {where}")
        where = f"{phase}: {content(names[0])}"
        badge = only(heading, "entry-kind")
        detail_badge = only(summary, "entry-kind")
        if set(badge.get("class").split()) != set(detail_badge.get("class").split()) or content(badge) != content(detail_badge):
            raise ValueError(f"visible call and implementation types differ at {where}")

        # The outer details of a composite can hide the whole child. Opening that
        # parent must reveal all of the child's I/O without opening the child itself.
        if (io in collapsed) != (call in collapsed):
            raise ValueError(f"input/output must be visible alongside its call at {where}")
        detail_nodes = set(call.iter()) if explicit else set()
        if any(el.tag == "details" or "hidden" in el.attrib or el.get("aria-hidden") == "true"
               for el in io.iter() if el not in detail_nodes):
            raise ValueError(f"hidden data inside call input/output at {where}")
        ancestor = step
        while ancestor is not stage:
            if "hidden" in ancestor.attrib or ancestor.get("aria-hidden") == "true":
                raise ValueError(f"hidden call template at {where}")
            ancestor = parents[ancestor]

        input_box = only(io, "card-input-route")
        output_box = only(io, "card-output-route")
        evidence = only(io, "memory-evidence")
        expected_io = [input_box, output_box, evidence]
        input_fields = ("read", "onchip")
        if explicit:
            if scope not in unclaimed_scopes:
                raise ValueError(f"details must be inside their own execution scope at {where}")
            unclaimed_scopes.remove(scope)
            ledger = check_execution_scope(scope, call, badge, where)
            for row in ledger:
                if len(fact_classes.intersection(row.get("class", "").split())) != 1:
                    raise ValueError(f"ambiguous execution field at {where}")
            unclaimed_facts.difference_update(ledger)
            expected_io.insert(1, scope)
            input_fields = ("read",)
        if list(io) != expected_io or not content(evidence):
            raise ValueError(f"expected Input, Output and one byte accounting basis at {where}")
        unclaimed_facts.remove(evidence)
        links = []
        for box, label, fields in ((input_box, "Input", input_fields),
                                   (output_box, "Output", ("write", "consumer", "handoff"))):
            title = only(box, "io-heading")
            lists = box.findall("dl")
            if not content(title).startswith(label) or len(lists) != 1 or list(box) != [title, lists[0]]:
                raise ValueError(f"expected a labeled {label} with its own fields at {where}")
            rows = lists[0]
            required = {only(rows, "memory-" + field) for field in fields}
            here = with_class(rows, "memory-link")
            if len(here) > 1 or len(rows) != len(fields) + len(here) or set(rows) != required.union(here):
                raise ValueError(f"duplicated or misplaced {label} fields at {where}")
            unclaimed_facts.difference_update(rows)
            for row in rows:
                if len(fact_classes.intersection(row.get("class", "").split())) != 1:
                    raise ValueError(f"ambiguous input/output role at {where}")
                if [el.tag for el in row] != ["dt", "dd"] or not all(content(el) for el in row):
                    raise ValueError(f"empty input/output fact at {where}")
            links.extend(here)
        if not links:
            raise ValueError(f"missing call communication classification at {where}")
        if len(links) == 2 and content(links[0].find("dd")) == content(links[1].find("dd")):
            raise ValueError(f"repeat of the same transfer in both Input and Output at {where}")
        for link in links:
            check_link_kind(link, where)
        check_implementation_details(call, io, where)
    if steps or unclaimed_io or unclaimed_facts or unclaimed_scopes:
        raise ValueError(f"call template or I/O fields without a matching invocation at {phase}")


def check_call_routes(stage, phase, calls, collapsed, *, implementation_details=False):
    """Each diagram belongs to exactly one call and is visible with that call closed."""
    parents = {child: parent for parent in stage.iter() for child in parent}
    routes = set(with_class(stage.iter(), "kernel-memory-route"))
    for call in calls:
        siblings = list(parents[call])
        index = siblings.index(call)
        summary = call.find("summary")
        names = [] if summary is None else [name for name in summary.iter("code") if "".join(name.itertext()).strip()]
        if not index or len(names) != 1 or siblings[index - 1] not in routes:
            raise ValueError(f"expected each call to follow its own data flow diagram at {phase}")
        route = siblings[index - 1]
        routes.remove(route)
        name = "".join(names[0].itertext()).strip()
        where = f"{phase}: {name}"
        if route.get("aria-label") != f"{name} 的数据流":
            raise ValueError(f"data flow diagram belongs to a different call at {where}")
        if (route in collapsed) != (call in collapsed):
            raise ValueError(f"expected the diagram to be visible with its call at {where}")
        # A nested call can live in a parent disclosure. Its own diagram must
        # still need no click once that parent is open.
        if any(el.tag == "details" or "hidden" in el.attrib for el in route.iter()):
            raise ValueError(f"hidden read/write data inside the diagram at {where}")
        for field in ("read", "write", "consumer", "handoff", "link", "onchip"):
            rows = with_class(route.iter(), "memory-" + field)
            values = [] if len(rows) != 1 else rows[0].findall("dd")
            if len(values) != 1 or not "".join(values[0].itertext()).strip():
                raise ValueError(f"missing or ambiguous {field} in the diagram at {where}")
        link = with_class(route.iter(), "memory-link")[0]
        check_link_kind(link, where)
        labels = with_class(route.iter(), "memory-evidence")
        if len(labels) != 1 or not "".join(labels[0].itertext()).strip():
            raise ValueError(f"missing byte accounting basis in the diagram at {where}")
        if implementation_details:
            check_implementation_details(call, route, where)
    if routes:
        raise ValueError(f"data flow diagram without a matching call at {phase}")


def check_stage_reading_order(stage, phase, calls, collapsed):
    """A reader can follow the forward path with the explanatory panels closed."""
    bodies = with_class(stage, "stage-body")
    if len(bodies) != 1:
        raise ValueError(f"expected one stage body at {phase}")
    body = list(bodies[0])
    execution = with_class(body, "stage-execution")
    support = with_class(body, "stage-support")
    if (len(execution) != 1 or len(support) != 1 or support[0].tag != "aside"
            or body.index(execution[0]) >= body.index(support[0])
            or execution[0] in collapsed or support[0] in collapsed):
        raise ValueError(f"expected an execution path followed by its own visible explanation at {phase}")

    headings = []
    for zone, role in ((execution[0], "section-exec-heading"), (support[0], "support-heading")):
        labels = with_class(zone, role)
        if len(labels) != 1 or labels[0] in collapsed:
            raise ValueError(f"expected a visible {role} at {phase}")
        headings.append("".join(labels[0].itertext()).strip())
    if not all(headings) or headings[0] == headings[1]:
        raise ValueError(f"give the execution path and its explanation distinct headings at {phase}")

    path = set(execution[0].iter())
    if not with_class(path, "stage-brief") or not with_class(support[0].iter(), "stage-transfers"):
        raise ValueError(f"put the stage summary on the execution path and its tensor map in the explanation at {phase}")
    nested = {child for call in calls for members in with_class(call, "kernel-members")
              for child in members.iter("details")}
    for call in calls:
        if call not in path or (call in collapsed and call not in nested):
            raise ValueError(f"source call hidden outside its parent invocation or off the execution path at {phase}")
        summary = call.find("summary")
        if (summary is None or not any((name.text or "").strip() for name in summary.iter("code"))
                or not any("".join(label.itertext()).strip() for label in with_class(summary, "call-detail-label"))):
            raise ValueError(f"expected a named source call with its own details label at {phase}")


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
    execution_format = ""
    if "kernel-execution-paths" in classes:
        execution_format = "call-execution"
    elif "kernel-backends" in classes:
        execution_format = "call-backend"
    uses_template = bool(execution_format) or has_call_kinds or bool({"kernel-card-template", "kernel-card"} & classes)
    collapsed = set()
    if continuous:
        for panel in section.iter("details"):
            if "open" not in panel.attrib:
                for child in panel:
                    if child.tag != "summary":
                        collapsed.update(child.iter())
    per_call_routes = any(node.get("data-memory-routes") == "per-call" for node in section.iter())
    implementation_details = any(node.get("data-call-details") == "implementation" for node in section.iter())
    integrated = any(node.get("data-call-io") == "integrated" for node in section.iter())
    markers = [el for el in section.iter() if "data-launch-boundaries" in el.attrib]
    explicit = bool(markers)
    has_scope_markup = bool(SCOPE_CLASSES & classes) or any("data-launch-scope" in el.attrib for el in section.iter())
    if explicit and (len(markers) != 1 or markers[0].get("data-launch-boundaries") != "explicit"
                     or markers[0].get("data-call-io") != "integrated"):
        raise ValueError("declare explicit execution scopes on the integrated I/O format marker")
    if has_scope_markup != explicit:
        raise ValueError("explicit execution scopes require a launch-boundaries marker")
    if "card-input-route" in classes and not integrated:
        raise ValueError("integrated call I/O requires its format marker")
    if integrated:
        if not implementation_details or per_call_routes or "kernel-memory-route" in classes:
            raise ValueError("integrated cards require implementation details and no separate per-call diagrams")
        stage_nodes = {el for stage in stages for el in stage.iter()}
        for el in section.iter():
            if el not in stage_nodes and ("data-launch-scope" in el.attrib or
                    (SCOPE_CLASSES | {"kernel-io"}).intersection(el.get("class", "").split())):
                raise ValueError("input/output or execution scope without an owning stage")
    if implementation_details and not (per_call_routes or integrated):
        raise ValueError("implementation-only details require a visible data flow diagram for every call")
    ordered = "stage-reading-order" in classes
    if ordered and not continuous:
        raise ValueError("the forward timeline must keep all stage headings visible")
    if "kernel-transfer-map" in classes or ordered:
        for node in section.iter():
            if "hidden" in node.attrib:
                collapsed.update(node.iter())
    if compact:
        opened = [el.get("data-phase") for el in stages if "open" in el.attrib]
        if opened != ["qkv"] or any(el.get("name") != "decode-stage" for el in stages):
            raise ValueError("expected a single open Q/KV panel in the named stage group")
    for phase, stage in zip(STAGES, stages):
        if "kernel-transfer-map" in classes:
            check_stage_transfers(stage, phase, collapsed)
        if execution_format:
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
        if integrated:
            check_integrated_calls(stage, phase, calls, collapsed, explicit=explicit)
        elif per_call_routes:
            check_call_routes(stage, phase, calls, collapsed, implementation_details=implementation_details)
        if ordered:
            check_stage_reading_order(stage, phase, calls, collapsed)
        if not implementation_details:
            for call in calls:
                check_kernel_card(call, phase, "kernel-contracts" in classes, uses_template,
                                  "kernel-input-examples" in classes, has_call_kinds, execution_format,
                                  has_transfer_map="kernel-transfer-map" in classes)
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
    if "kernel-execution-boundary" in classes and ".kernel-execution-boundary" not in css:
        raise ValueError("missing explicit kernel boundary styles")
    if "card-input-route" in classes and ".kernel-io" not in css:
        raise ValueError("missing integrated call I/O styles")
    if "kernel-memory-route" in classes and ".kernel-memory-route" not in css:
        raise ValueError("missing per-call memory diagram styles")
    if re.search(r"@import|url\s*\(|</style", css, re.I):
        raise ValueError("review external or unsupported CSS resource before export")
    return "\n".join(line.rstrip() for line in css.splitlines())


def export(source, article=None):
    if article is None:
        article = ArticleParser(source)
    if article.full_source is not None:
        raise ValueError("expected the complete article; use the lightweight entry as a command-line input")
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
        if page.locator("#compare-qkv .stage-transfers").count():
            stage = page.locator("#compare-qkv")
        else:
            stage = page.locator("#compare-qkv .stage-comparison")
        if not stage.count():
            stage = page.locator('.overview-stage[data-phase="qkv"]')
        if stage.locator(".kernel-io > .card-input-route, .kernel-memory-route").count():
            # Crop both comparison columns to the current shrink/expand pair.
            # The old sequence continues below it and remains intact in the HTML.
            table = stage.locator(".stage-comparison")
            header = table.locator(".operation-head")
            current = table.locator('.operation-row [data-pair-side="after"]').last
            # The Output follows details inside the explicit-scope design.
            # Use the enclosing card to keep the write and next reader in the crop.
            cards = current.locator(".kernel-step")
            final_call = cards.last if current.locator("[data-launch-scope]").count() else current.locator("details.kernel-drill").last
            top = header.bounding_box()["y"]
            last = final_call.bounding_box()
            height = ceil(last["y"] + last["height"] - top) + 4
            page.set_viewport_size({"width": 1200, "height": height + 16})
            header.evaluate("e => window.scrollTo(0, window.scrollY + e.getBoundingClientRect().top - 4)")
            box = header.bounding_box()
            clip = {"x": floor(box["x"]), "y": floor(box["y"]), "width": ceil(box["width"]), "height": height}
            image = page.screenshot(type="png", clip=clip, animations="disabled")
        else:
            image = stage.screenshot(type="png", animations="disabled")
        browser.close()
    png_path.write_bytes(image)


def read_article(path):
    """Read a historical self-contained article or a lightweight entry and its sibling."""
    source = path.read_text(encoding="utf-8")
    article = ArticleParser(source)
    if article.full_source is None:
        return source, article, path
    full = path.parent / article.full_source
    if full.is_symlink() or not full.is_file():
        raise ValueError(f"missing regular sibling file for article-full-source: {article.full_source}")
    if full == path:
        raise ValueError("article-full-source points back to the entry")
    source = full.read_text(encoding="utf-8")
    article = ArticleParser(source)
    if article.full_source is not None:
        raise ValueError("article-full-source must point directly to the complete article")
    return source, article, full


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="lightweight article entry or complete article HTML")
    parser.add_argument("output", type=Path, help="standalone comparison HTML")
    parser.add_argument("--preview", type=Path, metavar="PNG", help="also capture the Q/KV stage with Chromium")
    args = parser.parse_args()
    paths = [path.resolve() for path in (args.input, args.output, args.preview) if path]
    if len(set(paths)) != len(paths):
        parser.error("input, HTML output and PNG output must be different paths")
    try:
        source, article, full = read_article(paths[0])
        if full in paths[1:]:
            parser.error("the complete article cannot be used as an output path")
        document = export(source, article)
        args.output.write_bytes(document.encode("utf-8"))
        if args.preview:
            preview(args.output, args.preview)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    main()
