"""MCP-facing tool import.

``etsah import`` leaves an op with no inferred effect pure. This wrapper is
stricter: that op gets every effect, and the line is marked ``needs review``.
A person edits the ``eff`` line before the contract is used. Comments still
do not change the checker.
"""
from __future__ import annotations

import json
import re
from typing import Any

from etsah_import import ToolImportError, import_mcp, import_openai


_PURE_NOTE = "review: no effect was inferred. This op stays pure until you add an eff line."
_UNKNOWN_NOTE = (
    "needs review: side effects are unknown; defaulted to every effect "
    "(read, write, net, spend, emit)"
)
_EFFECT_KINDS = ("read", "write", "net", "spend", "emit")


def import_definitions(data: Any, kind: str = "auto") -> dict[str, Any]:
    """Convert MCP or OpenAI tool JSON into Etsah source and review flags.

    ``data`` may be a parsed object, a list, or a JSON string. A JSON-RPC
    ``tools/list`` envelope is unwrapped. ``kind`` is ``auto``, ``mcp``, or
    ``openai``.
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as ex:
            raise ToolImportError(f"invalid JSON: {ex.msg} (line {ex.lineno})") from ex
    data = _unwrap(data)
    selected = _kind(data, kind)
    if selected == "mcp":
        source = import_mcp(data)
    else:
        source = import_openai(data)
    source, defaulted = _default_unknown(source)
    ops = _ops(source)
    return {
        "kind": selected,
        "source": source,
        "needs_review": any(op["review"] for op in ops),
        "defaulted_unknown": defaulted,
        "ops": ops,
    }


def _unwrap(data: Any) -> Any:
    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, dict) and "tools" in result:
            return result
    return data


def _kind(data: Any, kind: str) -> str:
    selected = kind.strip().lower()
    if selected in {"mcp", "openai"}:
        return selected
    if selected not in {"", "auto"}:
        raise ToolImportError(f"unknown import kind {kind!r}; use auto, mcp, or openai")
    return _detect(data)


def _detect(data: Any) -> str:
    items = _items(data)
    saw_openai = False
    for item in items:
        if not isinstance(item, dict):
            continue
        if "inputSchema" in item or "input_schema" in item:
            return "mcp"
        function = item.get("function")
        if item.get("type") == "function" or isinstance(function, dict):
            saw_openai = True
        elif "parameters" in item and "inputSchema" not in item and "input_schema" not in item:
            saw_openai = True
    if saw_openai:
        return "openai"
    return "mcp"


def _items(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("tools"), list):
        return data["tools"]
    if isinstance(data, dict):
        return [data]
    return []


def _default_unknown(source: str) -> tuple[str, int]:
    pieces = re.split(r"(?=^op )", source, flags=re.M)
    rendered: list[str] = []
    count = 0
    for piece in pieces:
        if piece.startswith("op ") and _PURE_NOTE in piece and not _has_eff(piece):
            name = piece.split("(", 1)[0].removeprefix("op ").strip()
            effects = ", ".join(f"{kind}({name})" for kind in _EFFECT_KINDS)
            piece = piece.replace(
                "  # " + _PURE_NOTE,
                "  # " + _UNKNOWN_NOTE + "\n  eff " + effects,
                1,
            )
            count += 1
        rendered.append(piece)
    text = "".join(rendered)
    if text and not text.endswith("\n"):
        text += "\n"
    return text, count


def _has_eff(block: str) -> bool:
    return any(line.startswith("  eff ") for line in block.splitlines())


def _ops(source: str) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in source.splitlines():
        if line.startswith("op "):
            if current is not None:
                ops.append(current)
            name = line[3:].split("(", 1)[0].strip()
            current = {"name": name, "effects": [], "review": []}
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("# ") and "review" in stripped.lower():
            current["review"].append(stripped[2:].strip())
        elif stripped.startswith("eff "):
            current["effects"] = [part.strip() for part in stripped[4:].split(",") if part.strip()]
    if current is not None:
        ops.append(current)
    return ops
