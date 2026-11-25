"""Compatibility shim. Import etsah_mcp."""
from __future__ import annotations

import etsah_mcp as _mod

__version__ = _mod.__version__


def __getattr__(name: str):
    return getattr(_mod, name)


def __dir__() -> list[str]:
    return sorted(set(dir(_mod)) | {"__version__"})
