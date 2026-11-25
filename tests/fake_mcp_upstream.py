#!/usr/bin/env python3
"""Tiny MCP server used by the guard tests.

It records every tools/call to the path in FAKE_MCP_LOG, one JSON object per line.
The tools are fictional. Nothing is sent, charged, or labeled.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional


def read_message() -> Optional[dict[str, Any]]:
    line = sys.stdin.buffer.readline()
    if line == b"":
        return None
    payload = json.loads(line.decode("utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("expected a JSON object")
    return payload


def write_message(payload: dict[str, Any]) -> None:
    sys.stdout.buffer.write(json.dumps(payload).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def main() -> None:
    log_path = os.environ.get("FAKE_MCP_LOG", "")
    while True:
        message = read_message()
        if message is None:
            return
        if "id" not in message:
            continue
        msg_id = message["id"]
        method = message.get("method")
        if method == "initialize":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake-upstream", "version": "0"},
                    },
                }
            )
            continue
        if method == "tools/list":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [
                            {
                                "name": "tickets.read",
                                "description": "Read a fictional ticket at Harbor Lane Support.",
                                "inputSchema": {"type": "object", "properties": {}},
                            },
                            {
                                "name": "mail.send_reply",
                                "description": "Send a fictional reply.",
                                "inputSchema": {"type": "object", "properties": {}},
                            },
                            {
                                "name": "github.list_issues",
                                "description": "List fictional issues on example-org/widget.",
                                "inputSchema": {"type": "object", "properties": {}},
                            },
                        ]
                    },
                }
            )
            continue
        if method == "tools/call":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            name = params.get("name", "")
            if log_path:
                with open(log_path, "a", encoding="utf-8") as handle:
                    record = {"tool": name, "arguments": params.get("arguments") or {}}
                    handle.write(json.dumps(record) + "\n")
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"upstream:{name}"}],
                        "isError": False,
                    },
                }
            )
            continue
        if method == "ping":
            write_message({"jsonrpc": "2.0", "id": msg_id, "result": {}})
            continue
        write_message(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        )


if __name__ == "__main__":
    main()
