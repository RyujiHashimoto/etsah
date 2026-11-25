"""Compatibility shim. Import etsah_mcp.framing."""
from __future__ import annotations

import etsah_mcp.framing as _mod


def __getattr__(name: str):
    return getattr(_mod, name)


def __dir__() -> list[str]:
    return dir(_mod)
