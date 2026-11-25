"""Compatibility shim. The pattern compiler is etsahc.

``python3 proutc.py`` from this directory still runs, and warns.
"""
from __future__ import annotations

import sys


def _compiler():
    if __package__ in {None, ""}:
        import etsahc as mod
    else:
        from etsah_patterns import etsahc as mod
    return mod


def __getattr__(name: str):
    return getattr(_compiler(), name)


def __dir__() -> list[str]:
    return dir(_compiler())


def main():
    print("proutc is deprecated. 新名称は etsah", file=sys.stderr)
    return _compiler().main()


if __name__ == "__main__":
    raise SystemExit(main())
