"""Render a call's Input, pseudocode, Output, CTA and communication as HTML.

Input rows contain plain-text (variable and meaning, example value, reason) triples. The caller supplies the kernel name and decides
whether to display the body immediately or place it inside a details element.
render_call_template groups a call’s inputs, outputs and optional implementation
details. Use assets/kernel-card.css and assets/kernel-memory-route.css together.
"""
from html import escape
import re
from typing import Mapping, Optional, Sequence, Tuple, Union


KIND_LABELS = {
    "kernel": "显式 GPU kernel",
    "torch": "PyTorch 算子",
    "library": "后端库调用",
    "view": "张量视图",
    "copy": "数据复制",
    "composite": "组合流程",
    "collective": "集合通信",
    "example": "教学示意",
}


def kind_badge(kind_id: str) -> str:
    """Return a visible label for the level of execution represented by an entry."""
    if kind_id not in KIND_LABELS:
        raise ValueError(f"Unknown call kind: {kind_id!r}")
    return f'<span class="entry-kind kind-{kind_id}">{KIND_LABELS[kind_id]}</span>'


_SUBSCRIPT_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def physical_text(value: str) -> str:
    """Keep tensor subscripts legible with fonts that lack subscript digits."""
    return re.sub(
        r"[₀-₉]+",
        lambda match: "<sub>" + match[0].translate(_SUBSCRIPT_DIGITS) + "</sub>",
        escape(value),
    )


def _tensor(signature: str, note: str, size: str) -> str:
    parts = [f'<code class="tensor-signature">{escape(signature.strip())}</code>']
    if note.strip():
        parts.append(f"<p>{physical_text(note.strip())}</p>")
    parts.append(_size_note(size))
    return "".join(parts)


def _size_note(size: str) -> str:
    sizes = [line.strip() for line in size.replace("；", "\n").splitlines() if line.strip()]
    if not sizes:
        return ""
    lines = "".join(f"<span>{physical_text(line)}</span>" for line in sizes)
    return f'<div class="tensor-size">{lines}</div>'


InputRows = Sequence[Tuple[str, str, str]]
ExecutionPath = Tuple[str, str, str, str]


OutputPath = Tuple[str, str, str]


MemoryIO = Tuple[str, str, str, str, str, str]
_LOGICAL_VOLUME = "源码逻辑量 · HBM 和链路流量未实测"
_LINK_LABELS = {None: "卡间通信", "local": "本卡内", "host": "主机 ↔ GPU",
                "peer": "跨卡执行点", "conditional": "条件跨卡", "unknown": "通信待核"}


def _memory_field(key: str, label: str, value: str, modifier: str = "") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Memory route requires a nonempty {key}")
    return (f'<div class="memory-{key}{modifier}"><dt>{label}</dt>'
            f'<dd>{physical_text(value.strip())}</dd></div>')


def _memory_link(link_kind: Optional[str], link: str) -> str:
    if link_kind not in _LINK_LABELS:
        raise ValueError(f"Unknown memory route link kind: {link_kind!r}")
    modifier = f" memory-link-{link_kind}" if link_kind else ""
    return _memory_field("link", _LINK_LABELS[link_kind], link, modifier)


def _memory_evidence(evidence: str) -> str:
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("Memory route requires its accounting basis")
    return f'<small class="memory-evidence">{physical_text(evidence.strip())}</small>'


_SCOPE_HEADINGS = {
    "single": "一次 GPU kernel 发射",
    "continuation": "同一次 GPU kernel 发射 · 续段（新增 0 次）",
    "multiple": "多次 GPU 发射 · 组合流程",
    "conditional": "按运行条件选择执行路径",
    "view": "张量视图 · 仅更新元数据",
    "host": "主机端执行",
}


def _execution_scope(scope: str, kind_id: str, onchip: str, work: str,
                     note: str, details: str) -> str:
    if scope == "opaque":
        title = f"{KIND_LABELS[kind_id]} · 发射次数待确认"
    elif scope in _SCOPE_HEADINGS:
        title = _SCOPE_HEADINGS[scope]
    else:
        raise ValueError(f"Unknown launch scope: {scope!r}")
    hardware = scope in ("single", "continuation")
    section = "kernel-execution-boundary" if hardware else "call-implementation-scope"
    device_label = "片上暂存" if scope not in ("view", "host") else "设备访问"
    rows = []
    if work.strip():
        rows.append(f'<div class="scope-work"><dt>执行内容</dt><dd>{physical_text(work.strip())}</dd></div>')
    rows.append(_memory_field("onchip", device_label, onchip))
    if note.strip():
        rows.append(f'<div class="scope-note"><dt>范围说明</dt><dd>{physical_text(note.strip())}</dd></div>')
    return (f'<div class="{section}" data-launch-scope="{scope}">'
            f'<b class="scope-heading">{title}</b><dl>{"".join(rows)}</dl>{details}</div>')


def render_call_template(name: str, kind_id: str, io: MemoryIO, body: str, *,
                         link_kind: str, anchor: str = "", children: str = "",
                         evidence: str = _LOGICAL_VOLUME, link_side: Optional[str] = None,
                         input_link: Optional[str] = None, launch_scope: Optional[str] = None,
                         work: str = "", boundary_note: str = "") -> str:
    """Render Input, the implementation and its disclosure, then Output for one call.

    io holds plain text for (read, write, consumer, handoff, link, onchip). Name,
    anchor (a bare ID) and evidence are plain text too. Supply trusted rendered
    HTML for body (render_card_body with paired_route=True) and, optionally,
    children (the kernel-members element for a composite invocation).

    link_side assigns the link to "input" or "output". Single host transfers
    default to input (upload into the call), all others default to output.
    Override this for a download or an inbound peer operation. For a call with
    both a receive and a send, use input_link for the receive, io[4] for the send.

    Between Input and Output, launch_scope="single" marks one evidenced launch.
    Use "continuation" for a later part of that same launch. Use "multiple",
    "conditional", "view", "host", or "opaque" for other implementation scopes.
    Omitting launch_scope preserves the earlier I/O and disclosure layout.
    Within an explicit scope, work describes the computation. boundary_note
    states its conditions or names the launch counted earlier for a continuation.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("A call template requires its source call name")
    if not isinstance(io, (tuple, list)) or len(io) != 6:
        raise ValueError("A call template needs read, write, consumer, handoff, link and onchip")
    read, write, consumer, handoff, link, onchip = io
    input_rows = _memory_field("read", "读取", read)
    if launch_scope is None:
        input_rows += _memory_field("onchip", "片上暂存", onchip)
    output_rows = (_memory_field("write", "写入", write) + _memory_field("consumer", "下一读方", consumer)
                   + _memory_field("handoff", "交接搬运", handoff))
    if link_side not in (None, "input", "output"):
        raise ValueError("link_side must be input or output")
    if input_link is not None:
        if link_side == "input":
            raise ValueError("input_link describes the receive and io[4] must describe the send")
        input_rows += _memory_link(link_kind, input_link)
        output_rows += _memory_link(link_kind, link)
    elif link_side == "input" or (link_side is None and link_kind == "host"):
        input_rows += _memory_link(link_kind, link)
    else:
        output_rows += _memory_link(link_kind, link)
    title = f'<code>{escape(name.strip())}</code>'
    kind = kind_badge(kind_id)
    details_id = f' id="{escape(anchor, quote=True)}"' if anchor else ""
    purpose = escape(f"{name.strip()} · 调用详情", quote=True)
    details = (f'<details class="kernel-drill"{details_id}><summary aria-label="{purpose}">'
               f'{kind}{title}<span class="call-detail-label">调用详情</span></summary>'
               f'{body}{children}</details>')
    if launch_scope is None:
        scope_html = ""
        outer_details = details
    else:
        scope_html = _execution_scope(launch_scope, kind_id, onchip, work, boundary_note, details)
        outer_details = ""
    return (f'<div class="kernel-step"><div class="kernel-step-heading">{title}{kind}</div>'
            '<div class="kernel-io"><div class="card-input-route">'
            f'<b class="io-heading">Input · 从哪读</b><dl>{input_rows}</dl></div>{scope_html}'
            '<div class="card-output-route">'
            f'<b class="io-heading">Output · 写给谁</b><dl>{output_rows}</dl></div>'
            f'{_memory_evidence(evidence)}</div>{outer_details}</div>')


def render_memory_route(read: str, write: str, consumer: str, handoff: str, link: str, onchip: str, *,
                        evidence: str = _LOGICAL_VOLUME,
                        call_name: str = "", link_kind: Optional[str] = None) -> str:
    """Draw a standalone data path for older callers. All supplied values are plain text."""
    endpoints = (_memory_field("read", "从哪读", read) + _memory_field("write", "往哪写", write)
                 + _memory_field("consumer", "下一读方", consumer))
    edges = (_memory_field("handoff", "交接搬运", handoff) + _memory_link(link_kind, link)
             + _memory_field("onchip", "片上暂存", onchip))
    label = escape(f"{call_name} 的数据流" if call_name else "调用数据流")
    return (f'<div class="kernel-memory-route" role="group" aria-label="{label}">'
            f'<dl class="memory-endpoints">{endpoints}</dl><dl class="memory-edges">{edges}</dl>'
            f'{_memory_evidence(evidence)}</div>')


def render_transfer_path(record: Mapping[str, str]) -> str:
    """Render one tensor crossing a documented producer/consumer boundary."""
    endpoints = (("producer", "写入方 / 来源"), ("buffer", "张量与存放位置"), ("consumer", "下一读方"))
    accounting = (("write", "本步写入"), ("handoff", "交接新增搬运"),
                  ("next_read", "下游读取"), ("link", "通信"))
    for field, _ in endpoints + accounting:
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f"Transfer path requires a nonempty {field}")

    boxes = [
        f'<div class="transfer-{field}"><small>{label}</small><span>{physical_text(record[field])}</span></div>'
        for field, label in endpoints
    ]
    arrow = '<i class="transfer-arrow" aria-hidden="true">→</i>'
    facts = "".join(
        f'<div class="transfer-{field.replace("_", "-")}"><dt>{label}</dt><dd>{physical_text(record[field])}</dd></div>'
        for field, label in accounting
    )
    return ('<div class="transfer-path"><div class="transfer-route">' + arrow.join(boxes)
            + '</div><dl class="transfer-facts">' + facts + '</dl></div>')


def _output_path(path: OutputPath) -> str:
    roles = (("storage", "存储方式"), ("consumer", "下一读方"), ("handoff", "交接新增搬运"))
    if (not isinstance(path, (tuple, list)) or len(path) != len(roles)
            or any(not isinstance(v, str) or not v.strip() for v in path)):
        raise ValueError("Output path needs its storage, consumer and handoff cost")
    rows = "".join(f'<div class="output-{key}"><span>{label}</span> {physical_text(value.strip())}</div>'
                   for (key, label), value in zip(roles, path))
    return f'<div class="output-path">{rows}</div>'


def _execution_path(execution: ExecutionPath, *, include_work: bool = True) -> str:
    fields = (("api", "调用接口"), ("owner", "执行方"), ("work", "具体工作"), ("evidence", "设备端实现"))
    if (not isinstance(execution, (tuple, list)) or len(execution) != len(fields)
            or any(not isinstance(value, str) or not value.strip() for value in execution)):
        raise ValueError("An execution path needs an API, executor, operation and device evidence")
    rows = "".join(
        f'<div class="execution-{key}"><span class="execution-label">{label}</span> '
        f'<span class="execution-value">{physical_text(value.strip())}</span></div>'
        for (key, label), value in zip(fields, execution) if include_work or key != "work"
    )
    return f'<div class="call-execution">{rows}</div>'


def _input_rows(rows: InputRows, size: str) -> str:
    if not rows:
        raise ValueError("Kernel card requires at least one input row")
    items = []
    for index, row in enumerate(rows):
        if (not isinstance(row, (tuple, list)) or len(row) != 3
                or any(not isinstance(field, str) or not field.strip() for field in row)):
            raise ValueError(f"Input row {index + 1} needs a variable, an example and its reason")
        variable, example, reason = (field.strip() for field in row)
        symbol, separator, meaning = variable.partition(" · ")
        label = (f'<code>{escape(symbol)}</code> {physical_text(meaning)}'
                 if separator else physical_text(variable))
        items.append(
            f'<tr><th scope="row">{label}</th><td>'
            f'<span class="variable-example">{physical_text(example)}</span>'
            f'<span class="variable-reason">{physical_text(reason)}</span></td></tr>'
        )
    table = ('<table class="input-variables"><thead><tr><th scope="col">变量及含义</th>'
             '<th scope="col">本例取值与原因</th></tr></thead><tbody>'
             + "".join(items) + "</tbody></table>")
    extent = _size_note(size)
    if extent:
        table += f'<details class="input-extent"><summary>查看读取量与统计范围</summary>{extent}</details>'
    return table


def render_card_body(
    inputs: Union[str, InputRows],
    program: str,
    outputs: str = "",
    grid: str = "",
    traffic: str = "",
    *,
    kind: Optional[Tuple[str, str]] = None,
    backend: Optional[Tuple[str, str]] = None,
    execution: Optional[ExecutionPath] = None,
    output_path: Optional[OutputPath] = None,
    input_note: str = "",
    output_note: str = "",
    input_size: str = "",
    output_size: str = "",
    paired_route: bool = False,
    derivation: str = "",
) -> str:
    """Return a definition list for either a standalone card or a visible route. Signatures may span multiple lines.

    Pass one row per variable or a group of related indices. Give a traceable
    slice or range for data whose numeric contents are unknown. Existing
    callers can also pass a signature string with an input_note.

    Pass kind=(kind_id, dispatch_boundary) to distinguish a framework operator,
    a concrete launch, or another execution level. Use kind_badge(kind_id)
    in the caller's heading to show the type before the reader opens the card.

    execution=(api, executor, work, device_evidence) describes a source call
    independently from the implementation that handles it. State the actual
    device kernel or copy mechanism only when the evidence identifies it.
    Older callers may supply the two-field backend description instead.

    output_path=(storage, consumer, handoff) identifies how the consumer sees
    this output. Charge any copy at the operation that performs it.

    grid describes the unit of work or known dispatch boundary. traffic describes
    memory access, host copies and collectives as applicable. Size lines can state
    separate scopes, such as one CTA versus a complete launch.

    Set paired_route=True for the detail body in render_call_template, or when
    embedding an older standalone route. The visible Input and Output already
    supply the output, byte counts, handoff and communication. The disclosure adds pseudocode, implementation evidence,
    CTA layout and worked input coordinates. Use derivation only for an
    additional calculation whose operands or assumptions merit explanation.
    """
    required = [("program", program), ("grid", grid)]
    if not paired_route:
        required.extend([("outputs", outputs), ("traffic", traffic)])
        if derivation.strip():
            raise ValueError("A separate derivation belongs to a card beside a memory route")
    for name, value in required:
        if not value.strip():
            raise ValueError(f"Kernel card requires a nonempty {name} field")

    input_extent = "" if paired_route else input_size
    if isinstance(inputs, str):
        if not inputs.strip():
            raise ValueError("Kernel card requires a nonempty inputs field")
        input_html = _tensor(inputs, input_note, input_extent)
    else:
        input_html = _input_rows(inputs, input_extent)

    grid_html = physical_text(grid.strip())
    kind_class = ""
    if kind is not None:
        kind_id, boundary = kind
        if kind_id not in KIND_LABELS or not boundary.strip():
            raise ValueError(f"Kernel card needs a known kind and its dispatch boundary: {kind_id!r}")
        kind_class = f" kind-{kind_id}"
        grid_html = f'<span class="dispatch-boundary">{physical_text(boundary.strip())}</span>' + grid_html

    code = escape(program.strip("\r\n"))
    program_html = f'<pre class="kernel-pseudocode"><code>{code}</code></pre>'
    if execution is not None:
        if backend is not None:
            raise ValueError("Provide either execution or the legacy backend description")
        program_html = _execution_path(execution, include_work=not paired_route) + program_html
    elif backend is not None:
        backend_name, implementation = backend
        if not backend_name.strip() or not implementation.strip():
            raise ValueError("A backend needs both its name and its implementation path")
        program_html = (
            '<div class="call-backend">'
            f'<span class="backend-name">后端 · {physical_text(backend_name.strip())}</span> '
            f'<span class="backend-implementation">{physical_text(implementation.strip())}</span></div>'
            + program_html
        )
    if paired_route:
        fields = [
            ("program", "伪代码 · 执行过程", program_html),
            ("grid", "发射与 CTA 分工", grid_html),
            ("input", "变量与本例取值", input_html),
        ]
        if derivation.strip():
            fields.append(("derivation", "实现条件与推导", physical_text(derivation.strip())))
    else:
        output_html = _tensor(outputs, output_note, output_size)
        if output_path is not None:
            output_html += _output_path(output_path)
        fields = [
            ("input", "Input · 读哪些张量", input_html),
            ("program", "伪代码", program_html),
            ("output", "Output · 写到哪里", output_html),
            ("grid", "CTA · 发射与线程块分工", grid_html),
            ("traffic", "显存与跨卡通信", physical_text(traffic.strip())),
        ]
    body = "".join(
        f'<div class="card-{key}"><dt>{label}</dt><dd>{value}</dd></div>'
        for key, label, value in fields
    )
    layout_class = " paired-detail" if paired_route else ""
    return f'<dl class="kernel-card kernel-operands{kind_class}{layout_class}">{body}</dl>'
