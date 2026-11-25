"""JSON helpers for the browser playground.

This module does not check anything the checker does not already check.
ALLOW / BLOCK / ASK is a reading of that result: BLOCK is an error,
ASK means a declared effect still needs a human approval gate, and
ALLOW means the declaration has no such gate to wait for.
"""
from __future__ import annotations

import base64
import json
import zlib
from typing import Any, Optional

from prout import Expr, Parser, ProutError, format_effect
from prout_api import check_source
from prout_import import ToolImportError, import_mcp, import_openai


def check_text(source: str, allow_net: str = "") -> str:
    """Return the playground JSON for ``source``. ``allow_net`` is comma-separated."""
    return json.dumps(check_payload(source, allow_net), ensure_ascii=False)


def check_payload(source: str, allow_net: str = "") -> dict[str, Any]:
    """Parse and check ``source``, then label each task and op."""
    hosts = [part.strip() for part in allow_net.split(",") if part.strip()]
    try:
        program = Parser(source).parse()
    except ProutError as ex:
        diagnostic = {
            "code": "E-PARSE",
            "message": ex.message,
            "line": ex.line,
            "severity": "error",
        }
        return {
            "ok": False,
            "ast_hash": "",
            "audit_id": "",
            "diagnostics": [diagnostic],
            "plans": [],
            "verdicts": [],
            "summary": {"verdict": "BLOCK", "reason": ex.message},
        }
    result = check_source(source, allow_net=hosts or None)
    external, contract = _policy_sets(program)
    spans = _spans(program)
    diagnostics = list(result.diagnostics)
    verdicts: list[dict[str, Any]] = []
    ops_by_name = {op.name: op for op in program.ops}
    agents_by_name = {agent.name: agent for agent in program.agents}
    for task in program.tasks:
        block = _errors_in(diagnostics, spans, ("task", id(task)))
        if not block:
            for name in _called_ops(task):
                op = ops_by_name.get(name)
                if op is None:
                    continue
                block = _errors_in(diagnostics, spans, ("op", id(op)))
                if block:
                    break
        if not block:
            agent = agents_by_name.get(task.agent)
            if agent is not None:
                block = _errors_in(diagnostics, spans, ("agent", id(agent)))
        if block:
            verdict, reason = "BLOCK", block[0].message
        elif task.gates or any(_needs_gate(effect, external, contract) for effect in task.effects):
            verdict, reason = "ASK", _ask_task_reason(task, program.gates, external, contract)
        else:
            verdict, reason = "ALLOW", _allow_reason(task.effects)
        verdicts.append(
            {"kind": "task", "name": task.name, "verdict": verdict, "reason": reason, "line": task.line}
        )
    for op in program.ops:
        block = _errors_in(diagnostics, spans, ("op", id(op)))
        if block:
            verdict, reason = "BLOCK", block[0].message
        elif any(_needs_gate(effect, external, contract) for effect in op.effects):
            needed = [format_effect(effect) for effect in op.effects if _needs_gate(effect, external, contract)]
            verdict, reason = "ASK", "needs an approval gate before " + ", ".join(needed)
        else:
            verdict, reason = "ALLOW", _allow_reason(op.effects)
        verdicts.append(
            {"kind": "op", "name": op.name, "verdict": verdict, "reason": reason, "line": op.line}
        )
    errors = [item for item in diagnostics if item.severity == "error"]
    if errors:
        summary = {"verdict": "BLOCK", "reason": errors[0].message}
    elif any(item["verdict"] == "ASK" for item in verdicts):
        ask = next(item for item in verdicts if item["verdict"] == "ASK")
        summary = {"verdict": "ASK", "reason": ask["reason"]}
    elif verdicts:
        summary = {"verdict": "ALLOW", "reason": verdicts[0]["reason"]}
    else:
        summary = {"verdict": "ALLOW", "reason": "no task or op declared"}
    return {
        "ok": result.ok,
        "ast_hash": result.ast_hash,
        "audit_id": result.audit_id,
        "diagnostics": [item.as_dict() for item in diagnostics],
        "plans": [item.as_dict() for item in result.plans],
        "verdicts": verdicts,
        "summary": summary,
    }


def import_text(kind: str, raw: str) -> str:
    """Convert MCP or OpenAI tool JSON. The returned JSON has ``source`` or ``error``."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as ex:
        return json.dumps({"ok": False, "error": f"invalid JSON: {ex.msg} (line {ex.lineno})"})
    try:
        if kind == "mcp":
            source = import_mcp(data)
        elif kind == "openai":
            source = import_openai(data)
        else:
            return json.dumps({"ok": False, "error": "kind must be mcp or openai"})
    except ToolImportError as ex:
        return json.dumps({"ok": False, "error": str(ex)})
    return json.dumps({"ok": True, "source": source}, ensure_ascii=False)


def encode_share(source: str, allow_net: str = "", mode: str = "check", kind: str = "") -> str:
    """Compress a playground link payload with raw DEFLATE and url-safe base64.

    The browser hash is ``#`` plus this token. ``CompressionStream('deflate-raw')``
    reads the same bytes.
    """
    payload = json.dumps(
        {"s": source, "n": allow_net, "m": mode, "k": kind},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    compressor = zlib.compressobj(level=9, wbits=-15)
    data = compressor.compress(payload) + compressor.flush()
    token = base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
    return "z." + token


def decode_share(token: str) -> dict[str, str]:
    """Reverse :func:`encode_share`. A bad token raises ``ValueError``."""
    text = token[1:] if token.startswith("#") else token
    if not text.startswith("z."):
        raise ValueError("share link must start with z.")
    raw = text[2:]
    pad = "=" * (-len(raw) % 4)
    try:
        data = base64.urlsafe_b64decode(raw + pad)
        decompressor = zlib.decompressobj(wbits=-15)
        payload = decompressor.decompress(data) + decompressor.flush()
        obj = json.loads(payload.decode("utf-8"))
    except (ValueError, zlib.error, json.JSONDecodeError, UnicodeDecodeError) as ex:
        raise ValueError("share link could not be read") from ex
    if not isinstance(obj, dict) or not isinstance(obj.get("s"), str):
        raise ValueError("share link is missing source")
    return {
        "s": obj["s"],
        "n": obj["n"] if isinstance(obj.get("n"), str) else "",
        "m": obj["m"] if isinstance(obj.get("m"), str) else "check",
        "k": obj["k"] if isinstance(obj.get("k"), str) else "",
    }


def _policy_sets(program: Any) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    external: set[tuple[str, str]] = set()
    contract: set[tuple[str, str]] = set()
    for policy in program.policies:
        external.update(policy.external)
        contract.update(policy.contract)
    return external, contract


def _needs_gate(
    effect: tuple[str, str],
    external: set[tuple[str, str]],
    contract: set[tuple[str, str]],
) -> bool:
    """Same rule as ``Checker._needs_gate``: spend, external emit or net, contract write."""
    kind = effect[0]
    if kind == "spend":
        return True
    if kind in ("emit", "net") and effect in external:
        return True
    if kind == "write" and effect in contract:
        return True
    return False


def _spans(program: Any) -> list[tuple[int, int, str, int]]:
    """Half-open line spans for each declaration, keyed by ``id(node)``."""
    items: list[tuple[int, str, Any]] = []
    for op in program.ops:
        items.append((op.line, "op", op))
    for task in program.tasks:
        items.append((task.line, "task", task))
    for agent in program.agents:
        items.append((agent.line, "agent", agent))
    for gate in program.gates:
        items.append((gate.line, "gate", gate))
    for policy in program.policies:
        items.append((policy.line, "policy", policy))
    for flow in program.flows:
        items.append((flow.line, "flow", flow))
    for typedef in program.types:
        items.append((typedef.line, "type", typedef))
    items.sort(key=lambda item: item[0])
    spans: list[tuple[int, int, str, int]] = []
    for index, (line, kind, node) in enumerate(items):
        end = items[index + 1][0] if index + 1 < len(items) else 10**9
        spans.append((line, end, kind, id(node)))
    return spans


def _errors_in(diagnostics: list[Any], spans: list[tuple[int, int, str, int]], key: tuple[str, int]) -> list[Any]:
    kind, node_id = key
    span = next((item for item in spans if item[2] == kind and item[3] == node_id), None)
    if span is None:
        return []
    start, end = span[0], span[1]
    return [item for item in diagnostics if item.severity == "error" and start <= item.line < end]


def _called_ops(task: Any) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for stmt in task.body:
        for name in _call_names(stmt.expr):
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _call_names(expr: Optional[Expr]) -> list[str]:
    if expr is None:
        return []
    names: list[str] = []
    if expr.kind == "call":
        names.append(expr.name)
    for child in (expr.left, expr.right, expr.base):
        names.extend(_call_names(child))
    for arg in expr.args:
        names.extend(_call_names(arg))
    return names


def _ask_task_reason(task: Any, gates: list[Any], external: set[tuple[str, str]], contract: set[tuple[str, str]]) -> str:
    if task.gates:
        by_name = {gate.name: gate for gate in gates}
        parts: list[str] = []
        for name in task.gates:
            gate = by_name.get(name)
            if gate is None:
                parts.append(f"needs approval gate {name}")
                continue
            covered = [format_effect(effect) for effect in gate.before if _needs_gate(effect, external, contract)]
            shown = ", ".join(covered) if covered else ", ".join(format_effect(effect) for effect in gate.before)
            who = gate.approver or "a person"
            parts.append(f"asks {who} at {name} before {shown or 'the gated effect'}")
        return "; ".join(parts)
    needed = [format_effect(effect) for effect in task.effects if _needs_gate(effect, external, contract)]
    return "needs an approval gate before " + ", ".join(needed)


def _allow_reason(effects: list[tuple[str, str]]) -> str:
    if not effects:
        return "no approval gate is required"
    shown = ", ".join(format_effect(effect) for effect in effects)
    return f"no approval gate is required for {shown}"


__all__ = [
    "check_payload",
    "check_text",
    "decode_share",
    "encode_share",
    "import_text",
]
