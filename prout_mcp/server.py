"""Compatibility shim. Import etsah_mcp.server.

The prout-mcp command warns, then serves the same stdio checker.
"""
from __future__ import annotations

import sys

import etsah_mcp.server as _mod


def __getattr__(name: str):
    return getattr(_mod, name)


def __dir__() -> list[str]:
    return sorted(set(dir(_mod)) | {"main"})


def main(argv: list[str] | None = None) -> None:
    print("prout-mcp is deprecated. 新名称は etsah", file=sys.stderr)
    _mod.main(argv)


if __name__ == "__main__":
    main()
