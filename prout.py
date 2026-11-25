"""Compatibility shim. Import etsah.

The language is Etsah. This module remains for one minor version.
Running it as a command warns that the new name is etsah.
"""
from __future__ import annotations

import sys

import etsah as _etsah


def __getattr__(name: str):
    return getattr(_etsah, name)


def __dir__() -> list[str]:
    return sorted(set(dir(_etsah)) | {"main"})


def main() -> None:
    print("prout is deprecated. 新名称は etsah", file=sys.stderr)
    _etsah.main()


if __name__ == "__main__":
    main()
