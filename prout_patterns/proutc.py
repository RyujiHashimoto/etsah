"""Installed compatibility command. Use etsahc."""
from __future__ import annotations

import sys

from etsah_patterns.etsahc import main as _main


def main():
    print("proutc is deprecated. 新名称は etsah", file=sys.stderr)
    return _main()
