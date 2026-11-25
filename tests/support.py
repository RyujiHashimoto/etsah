"""Shared helpers for the Etsah unit tests.

Tests are stdlib ``unittest`` and stay pytest-compatible. Snippets are
dedented so cases can be indented with the surrounding Python, while the
languages themselves keep a 2-space indent.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ROOT / "patterns"

for _path in (ROOT, PATTERNS):
    entry = str(_path)
    if entry not in sys.path:
        sys.path.insert(0, entry)

import etsah  # noqa: E402
from etsah import Checker, Parser  # noqa: E402


def snippet(text: str) -> str:
    """Dedent a triple-quoted program and give it a trailing newline."""
    return textwrap.dedent(text).strip() + "\n"


def parse(text: str) -> etsah.Program:
    return Parser(snippet(text)).parse()


def check(text: str, **kwargs) -> list[etsah.Diagnostic]:
    return Checker(parse(text), **kwargs).check()


def codes(diags, severity: str | None = None) -> list[str]:
    return [d.code for d in diags if severity is None or d.severity == severity]
