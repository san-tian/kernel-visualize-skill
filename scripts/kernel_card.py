"""Render a kernel's Input, pseudocode, Output, CTA and communication as HTML.

Input rows contain plain-text (variable and meaning, example value, reason) triples. The caller supplies the kernel name and decides
whether to display the body immediately or place it inside a details element.
Pair the returned fragment with assets/kernel-card.css.
"""
from html import escape
import re
from typing import Sequence, Tuple, Union


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
    outputs: str,
    grid: str,
    traffic: str,
    *,
    input_note: str = "",
    output_note: str = "",
    input_size: str = "",
    output_size: str = "",
) -> str:
    """Return a self-contained definition list. Signatures may span multiple lines.

    Pass one row per variable or a group of related indices. Give a traceable
    slice or range for data whose numeric contents are unknown. Existing
    callers can also pass a signature string with an input_note.

    grid describes the unit of work or known dispatch boundary. traffic describes
    memory access, host copies and collectives as applicable. Size lines can state
    separate scopes, such as one CTA versus a complete launch.
    """
    for name, value in (
        ("program", program), ("outputs", outputs),
        ("grid", grid), ("traffic", traffic),
    ):
        if not value.strip():
            raise ValueError(f"Kernel card requires a nonempty {name} field")

    if isinstance(inputs, str):
        if not inputs.strip():
            raise ValueError("Kernel card requires a nonempty inputs field")
        input_html = _tensor(inputs, input_note, input_size)
    else:
        input_html = _input_rows(inputs, input_size)

    code = escape(program.strip("\r\n"))
    fields = (
        ("input", "Input · 读哪些张量", input_html),
        ("program", "伪代码", f'<pre class="kernel-pseudocode"><code>{code}</code></pre>'),
        ("output", "Output · 写到哪里", _tensor(outputs, output_note, output_size)),
        ("grid", "CTA · 线程块分工", physical_text(grid.strip())),
        ("traffic", "显存与跨卡通信", physical_text(traffic.strip())),
    )
    body = "".join(
        f'<div class="card-{key}"><dt>{label}</dt><dd>{value}</dd></div>'
        for key, label, value in fields
    )
    return f'<dl class="kernel-card kernel-operands">{body}</dl>'
