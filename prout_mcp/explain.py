"""Compatibility shim. Import etsah_mcp.explain."""
from __future__ import annotations

import etsah_mcp.explain as _mod


def __getattr__(name: str):
    return getattr(_mod, name)


def __dir__() -> list[str]:
    return dir(_mod)
