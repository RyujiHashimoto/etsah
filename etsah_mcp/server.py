"""MCP stdio server for the Etsah checker.

The process speaks newline-delimited JSON-RPC 2.0 on stdin and stdout.
Diagnostics and any other log stay off stdout. The four tools are read-only:
they check source, they do not run an agent's tools.
"""
from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO, Optional

from etsah_import import ToolImportError

from etsah_mcp import __version__
from etsah_mcp.decide import check_plan, check_report
from etsah_mcp.explain import explain_code
from etsah_mcp.framing import read_message, write_message
from etsah_mcp.imports import import_definitions


_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

_INSTRUCTIONS = (
    "Etsah checks a contract before an agent acts. "
    "etsah_check reads source and returns ALLOW, BLOCK, or ASK for each task. "
    "etsah_import_tools turns an MCP or OpenAI tool list into op declarations and "
    "marks unknown side effects for review. "
    "etsah_explain describes an error code such as E-POLICY. "
    "etsah_check_plan reviews a proposed sequence of tool calls. "
    "These tools do not send mail, move money, or call another server."
)


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


def _tool(name: str, description: str, schema: dict[str, Any], title: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": schema,
        "annotations": {"title": title, **_READ_ONLY},
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "etsah_check",
        "Check Etsah source. Returns ok, diagnostics (code, line, message), ast_hash, "
        "and one plan per task with an ALLOW, BLOCK, or ASK verdict.",
        _schema(
            {
                "source": {"type": "string", "description": "Etsah contract source."},
                "allow_net": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Hosts that may appear in net effects. Replaces the default list, which is stripe. An empty list keeps the default.",
                },
                "deny_net": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Hosts that are denied even when they are on the allow list.",
                },
            },
            ["source"],
        ),
        "Check an Etsah contract",
    ),
    _tool(
        "etsah_import_tools",
        "Convert an MCP tools/list document or OpenAI tool definitions into Etsah op declarations. "
        "Unknown side effects become read, write, net, spend, and emit, and are marked needs review.",
        _schema(
            {
                "tools": {
                    "description": "A tool object, a list of tools, an object with a tools list, a tools/list result, or the same value as a JSON string.",
                    "oneOf": [{"type": "object"}, {"type": "array"}, {"type": "string"}],
                },
                "kind": {
                    "type": "string",
                    "enum": ["auto", "mcp", "openai"],
                    "description": "Which importer to use. auto detects inputSchema (MCP) versus function tools (OpenAI).",
                },
            },
            ["tools"],
        ),
        "Import tool definitions",
    ),
    _tool(
        "etsah_explain",
        "Explain an Etsah diagnostic code such as E-POLICY, and suggest a fix.",
        _schema(
            {"code": {"type": "string", "description": "A diagnostic code, for example E-POLICY."}},
            ["code"],
        ),
        "Explain an error code",
    ),
    _tool(
        "etsah_check_plan",
        "Review a proposed sequence of tool calls against a contract. "
        "Each step is ALLOW, BLOCK, or ASK. ok is true only when every step is ALLOW. "
        "Arguments are accepted and are not evaluated; the decision uses the tool name, "
        "declared effects, the agent allow list, the net allow list, and named gates.",
        _schema(
            {
                "source": {"type": "string", "description": "Etsah contract source."},
                "agent": {
                    "type": "string",
                    "description": "Agent whose allow list to use. Required when the contract declares more than one agent and the tool is not called by exactly one of them.",
                },
                "steps": {
                    "type": "array",
                    "description": "Proposed tool calls, in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string"},
                            "name": {"type": "string", "description": "Alias of tool."},
                            "arguments": {"type": "object"},
                        },
                    },
                },
                "allow_net": {"type": "array", "items": {"type": "string"}},
                "deny_net": {"type": "array", "items": {"type": "string"}},
            },
            ["source", "steps"],
        ),
        "Check a proposed plan",
    ),
]

_HANDLERS = {
    "etsah_check": "check",
    "etsah_import_tools": "import",
    "etsah_explain": "explain",
    "etsah_check_plan": "plan",
}


def serve(stdin: Optional[BinaryIO] = None, stdout: Optional[BinaryIO] = None) -> None:
    """Serve until stdin closes."""
    incoming = stdin if stdin is not None else sys.stdin.buffer
    outgoing = stdout if stdout is not None else sys.stdout.buffer
    while True:
        try:
            message = read_message(incoming)
        except ValueError as ex:
            write_message(outgoing, rpc_error(None, -32700, str(ex)))
            continue
        if message is None:
            return
        response = dispatch(message)
        if response is not None:
            try:
                write_message(outgoing, response)
            except BrokenPipeError:
                return


def dispatch(message: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return a JSON-RPC response, or None when the message is a notification."""
    if message.get("jsonrpc") not in (None, "2.0"):
        return rpc_error(message.get("id"), -32600, "invalid request")
    method = message.get("method")
    if not isinstance(method, str):
        if "id" in message:
            return rpc_error(message.get("id"), -32600, "invalid request")
        return None
    params = message.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        if "id" in message:
            return rpc_error(message["id"], -32602, "params must be an object")
        return None
    has_id = "id" in message
    if method == "initialize":
        return rpc_ok(message.get("id"), initialize_result(params)) if has_id else None
    if method in {"notifications/initialized", "initialized"}:
        return None
    if method == "ping":
        return rpc_ok(message["id"], {}) if has_id else None
    if method == "tools/list":
        return rpc_ok(message["id"], {"tools": TOOLS}) if has_id else None
    if method == "tools/call":
        if not has_id:
            return None
        return rpc_ok(message["id"], call_tool(params))
    if has_id:
        return rpc_error(message["id"], -32601, f"method not found: {method}")
    return None


def initialize_result(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocolVersion": protocol_version(params),
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "etsah", "version": __version__},
        "instructions": _INSTRUCTIONS,
    }


def protocol_version(params: dict[str, Any]) -> str:
    """Echo the client's version when it sends one.

    The tools methods used here are the same in the 2024-11-05, 2025-03-26,
    and 2025-06-18 revisions. Desktop clients abort when the server answers
    with a version they did not offer.
    """
    requested = params.get("protocolVersion")
    if isinstance(requested, str) and requested.strip():
        return requested.strip()
    return "2024-11-05"


_LEGACY_TOOLS = {
    "prout_check": "etsah_check",
    "prout_import_tools": "etsah_import_tools",
    "prout_explain": "etsah_explain",
    "prout_check_plan": "etsah_check_plan",
}


def call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if isinstance(name, str) and name in _LEGACY_TOOLS:
        print(f"{name} is deprecated. 新名称は etsah", file=sys.stderr)
        name = _LEGACY_TOOLS[name]
    arguments = params.get("arguments", {})
    if arguments is None:
        arguments = {}
    if not isinstance(name, str) or name not in _HANDLERS:
        return tool_error(f"unknown tool {name!r}")
    if not isinstance(arguments, dict):
        return tool_error("arguments must be an object")
    try:
        if name == "etsah_check":
            payload = tool_check(arguments)
        elif name == "etsah_import_tools":
            payload = tool_import(arguments)
        elif name == "etsah_explain":
            payload = tool_explain(arguments)
        else:
            payload = tool_plan(arguments)
    except ToolImportError as ex:
        return tool_error(str(ex))
    except (TypeError, ValueError) as ex:
        return tool_error(str(ex))
    return tool_ok(payload)


def tool_check(arguments: dict[str, Any]) -> dict[str, Any]:
    source = arguments.get("source")
    if not isinstance(source, str):
        raise ValueError("source must be a string")
    return check_report(
        source,
        allow_net=_string_list(arguments.get("allow_net"), "allow_net"),
        deny_net=_string_list(arguments.get("deny_net"), "deny_net"),
    )


def tool_import(arguments: dict[str, Any]) -> dict[str, Any]:
    if "tools" not in arguments:
        raise ValueError("tools is required")
    kind = arguments.get("kind", "auto")
    if not isinstance(kind, str):
        raise ValueError("kind must be a string")
    return import_definitions(arguments["tools"], kind)


def tool_explain(arguments: dict[str, Any]) -> dict[str, Any]:
    code = arguments.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("code must be a non-empty string")
    return explain_code(code)


def tool_plan(arguments: dict[str, Any]) -> dict[str, Any]:
    source = arguments.get("source")
    steps = arguments.get("steps")
    if not isinstance(source, str):
        raise ValueError("source must be a string")
    if not isinstance(steps, list):
        raise ValueError("steps must be a list")
    agent = arguments.get("agent", "")
    if agent is None:
        agent = ""
    if not isinstance(agent, str):
        raise ValueError("agent must be a string")
    return check_plan(
        source,
        steps,
        agent=agent,
        allow_net=_string_list(arguments.get("allow_net"), "allow_net"),
        deny_net=_string_list(arguments.get("deny_net"), "deny_net"),
    )


def _string_list(value: Any, label: str) -> Optional[list[str]]:
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of strings")
    return value


def tool_ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "structuredContent": payload,
        "isError": False,
    }


def tool_error(message: str) -> dict[str, Any]:
    payload = {"error": message}
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": payload,
        "isError": True,
    }


def rpc_ok(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def rpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def main(argv: Optional[list[str]] = None) -> None:
    """``etsah-mcp`` entry point. ``etsah-mcp guard`` starts the proxy."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "guard":
        from etsah_mcp.guard import main as guard_main

        guard_main(args[1:])
        return
    if args and args[0] == "approve":
        from etsah_mcp.approval import main as approve_main

        approve_main(args[1:])
        return
    if args and args[0] in {"-h", "--help"}:
        print(
            "usage: etsah-mcp [mcp]\n"
            "       etsah-mcp guard --contract FILE [--agent NAME] "
            "[--allow-net HOST] [--deny-net HOST] [--approval-key-file FILE] "
            "[--nonce-file FILE] [--approval-token TOKEN] [--allow-legacy-approval-token] -- UPSTREAM...\n"
            "       etsah-mcp approve --contract FILE --gate NAME --op NAME --approver ID "
            "(--args JSON | --args-file FILE) (--ttl SECONDS | --expires-at UNIX)"
        )
        return
    if args and args[0] != "mcp":
        print(f"[ERROR] unknown command {args[0]!r}", file=sys.stderr)
        sys.exit(2)
    serve()
