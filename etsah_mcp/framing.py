"""Newline-delimited JSON-RPC framing for MCP stdio.

Messages on the wire are one JSON object per line. A client that still sends
``Content-Length`` headers is accepted on input. Replies are always one line,
which is the stdio transport current clients read.
"""
from __future__ import annotations

import json
from typing import Any, BinaryIO, Optional


def read_message(stream: BinaryIO) -> Optional[dict[str, Any]]:
    """Read one JSON-RPC message. ``None`` means the stream closed."""
    while True:
        line = stream.readline()
        if line == b"":
            return None
        if not line.strip():
            continue
        if line.lower().startswith(b"content-length:"):
            return _read_framed(stream, line)
        try:
            payload = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as ex:
            raise ValueError(f"invalid JSON-RPC message: {ex}") from ex
        if isinstance(payload, dict):
            return payload
        raise ValueError("JSON-RPC message must be an object")


def write_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    """Write one JSON object and flush. The object itself contains no raw newline."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    stream.write(text.encode("utf-8") + b"\n")
    stream.flush()


def _read_framed(stream: BinaryIO, first: bytes) -> dict[str, Any]:
    try:
        length = int(first.split(b":", 1)[1].strip())
    except ValueError as ex:
        raise ValueError("invalid Content-Length header") from ex
    if length < 0:
        raise ValueError("invalid Content-Length header")
    while True:
        header = stream.readline()
        if header == b"":
            raise ValueError("unexpected end of stream in MCP headers")
        if header in (b"\n", b"\r\n"):
            break
    body = stream.read(length)
    if len(body) != length:
        raise ValueError("unexpected end of stream in MCP body")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as ex:
        raise ValueError(f"invalid JSON-RPC message: {ex}") from ex
    if not isinstance(payload, dict):
        raise ValueError("JSON-RPC message must be an object")
    return payload
