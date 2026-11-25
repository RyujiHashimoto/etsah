"""Build docs/check-demo.svg from real ``etsah check`` stdout.

The two terminals are the broken support-reply contract and the same
task after it names ``gate approve_reply``. Headings are labels. Every
line under a prompt is the checker's stdout, unmodified.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SVG_PATH = ROOT / "docs" / "check-demo.svg"

BEFORE_CMD = "python3 etsah.py check examples/agents/support_reply_broken.etsah"
AFTER_CMD = "python3 etsah.py check examples/agents/support_reply.etsah"


def capture(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "etsah.py"), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.stderr:
        raise RuntimeError(proc.stderr)
    return proc.returncode, proc.stdout


def render_svg() -> str:
    before_code, before = capture(["check", "examples/agents/support_reply_broken.etsah"])
    after_code, after = capture(["check", "examples/agents/support_reply.etsah"])
    if before_code == 0:
        raise RuntimeError("broken support reply unexpectedly passed")
    if after_code != 0:
        raise RuntimeError("gated support reply unexpectedly failed")
    if "E-POLICY" not in before or "emit to an external party has no approval gate" not in before:
        raise RuntimeError("broken stdout is missing E-POLICY")
    if "OK — no issues" not in after or "gate: approve_reply" not in after:
        raise RuntimeError("gated stdout is missing the approval gate")
    return _svg(before, after)


def _svg(before: str, after: str) -> str:
    before_lines = [f"$ {BEFORE_CMD}", *before.splitlines()]
    after_lines = [f"$ {AFTER_CMD}", *after.splitlines()]
    width = 1080
    margin = 32
    panel_width = width - margin * 2
    line_h = 18
    header_h = 36
    pad_y = 14

    def panel_height(count: int) -> int:
        return header_h + pad_y * 2 + line_h * count

    before_h = panel_height(len(before_lines))
    after_h = panel_height(len(after_lines))
    gap = 28
    top = 28
    height = top + 22 + before_h + gap + 22 + after_h + 36
    before_y = top + 22
    after_y = before_y + before_h + gap + 22

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img">',
        "<title>etsah check fails with E-POLICY, then passes after the task names an approval gate</title>",
        "<desc>Recorded stdout. The first command is the support-reply task with no gate. "
        "The second is the same task after gate approve_reply is named. The checker does not send mail.</desc>",
        "<style>",
        ".hl-bad { animation: show-bad 10s ease-in-out infinite; }",
        ".hl-ok { opacity: 0; animation: show-ok 10s ease-in-out infinite; }",
        ".cursor { animation: blink 1.1s steps(1) infinite; }",
        "@keyframes show-bad { 0%, 42% { opacity: 1; } 52%, 90% { opacity: 0.2; } 100% { opacity: 1; } }",
        "@keyframes show-ok { 0%, 42% { opacity: 0; } 52%, 90% { opacity: 1; } 100% { opacity: 0; } }",
        "@keyframes blink { 50% { opacity: 0; } }",
        "</style>",
        f'<rect width="{width}" height="{height}" fill="#f4f1ea"/>',
        f'<text x="{margin}" y="{top}" fill="#5c564c" font-family="ui-sans-serif, Segoe UI, sans-serif" font-size="15">Before — the task emails the customer with no gate</text>',
        *_panel(margin, before_y, panel_width, before_h, "etsah check  exit 1", before_lines, "bad"),
        f'<text x="{margin}" y="{before_y + before_h + 22}" fill="#5c564c" font-family="ui-sans-serif, Segoe UI, sans-serif" font-size="15">After — the task names gate approve_reply</text>',
        *_panel(margin, after_y, panel_width, after_h, "etsah check  exit 0", after_lines, "ok"),
        f'<text x="{margin}" y="{height - 14}" fill="#8a8175" font-family="ui-sans-serif, Segoe UI, sans-serif" font-size="13">stdout from the commands above. The checker does not send the email.</text>',
        "</svg>",
        "",
    ]
    return "\n".join(parts)


def _panel(x: int, y: int, width: int, height: int, title: str, lines: list[str], mark: str) -> list[str]:
    header = 36
    line_h = 18
    text_x = x + 16
    first_baseline = y + header + 26
    chunks = [
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="10" fill="#231f1b"/>',
        f'<rect x="{x}" y="{y}" width="{width}" height="{header}" rx="10" fill="#3a342c"/>',
        f'<rect x="{x}" y="{y + 20}" width="{width}" height="{header - 20}" fill="#3a342c"/>',
        f'<circle cx="{x + 18}" cy="{y + 18}" r="5" fill="#e25b45"/>',
        f'<circle cx="{x + 34}" cy="{y + 18}" r="5" fill="#d2a85a"/>',
        f'<circle cx="{x + 50}" cy="{y + 18}" r="5" fill="#7dba6a"/>',
        f'<text x="{x + 68}" y="{y + 23}" fill="#d9d0c3" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="13">{_esc(title)}</text>',
    ]
    for index, line in enumerate(lines):
        baseline = first_baseline + index * line_h
        if mark == "bad" and "E-POLICY" in line:
            chunks.append(
                f'<rect class="hl-bad" x="{x + 8}" y="{baseline - 13}" width="{width - 16}" height="18" rx="3" fill="#5c2a24"/>'
            )
        if mark == "ok" and line.startswith("OK"):
            chunks.append(
                f'<rect class="hl-ok" x="{x + 8}" y="{baseline - 13}" width="{width - 16}" height="18" rx="3" fill="#1e4630"/>'
            )
        fill = "#e7e1d5"
        if line.startswith("$"):
            fill = "#e2c27a"
        elif "E-POLICY" in line or line.startswith("[ERROR]"):
            fill = "#ffb4a8"
        elif line.startswith("OK"):
            fill = "#b7e3ad"
        elif line.startswith("audit id:"):
            fill = "#a3988c"
        chunks.append(
            f'<text x="{text_x}" y="{baseline}" fill="{fill}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="13">{_esc(line) or " "}</text>'
        )
    if mark == "ok" and lines:
        cursor_x = text_x + max(8 * len(lines[0]), 24)
        baseline = first_baseline
        chunks.append(f'<rect class="cursor" x="{cursor_x}" y="{baseline - 12}" width="8" height="15" fill="#e2c27a"/>')
    return chunks


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main() -> None:
    SVG_PATH.write_text(render_svg(), encoding="utf-8")


if __name__ == "__main__":
    main()
