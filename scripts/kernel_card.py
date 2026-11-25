"""Render a kernel's Input, pseudocode, Output, CTA and communication as HTML.

All arguments are plain text. The caller supplies the kernel name and decides
whether to display the body immediately or place it inside a details element.
Pair the returned fragment with assets/kernel-card.css.
"""
from html import escape
import re


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
    sizes = [line.strip() for line in size.replace("；", "\n").splitlines() if line.strip()]
    if sizes:
        lines = "".join(f"<span>{physical_text(line)}</span>" for line in sizes)
        parts.append(f'<div class="tensor-size">{lines}</div>')
    return "".join(parts)


def render_card_body(
    inputs: str,
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

    grid describes the unit of work or known dispatch boundary. traffic describes
    memory access, host copies and collectives as applicable. Size lines can state
    separate scopes, such as one CTA versus a complete launch.
    """
    for name, value in (
        ("inputs", inputs), ("program", program), ("outputs", outputs),
        ("grid", grid), ("traffic", traffic),
    ):
        if not value.strip():
            raise ValueError(f"Kernel card requires a nonempty {name} field")

    code = escape(program.strip("\r\n"))
    fields = (
        ("input", "Input · 读哪些张量", _tensor(inputs, input_note, input_size)),
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
