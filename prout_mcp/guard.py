"""Stdio MCP proxy that checks each tools/call against a PROUT contract.

``prout guard --contract file.prout -- <upstream command>`` answers the MCP
client itself and forwards ``tools/list`` and allowed ``tools/call`` messages
to the upstream server. BLOCK is a tool result with ``isError`` true. ASK is
a tool result that asks for a person, and the upstream call is not made,
unless ``PROUT_APPROVAL_TOKEN`` or ``--approval-token`` is a non-empty string.
The token is a presence flag for this first step. It does not override BLOCK,
and it is not written to the log.

Each decision is one JSON object on stderr, including ``audit_id``.
"""
from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Optional

from prout_mcp import __version__
from prout_mcp.decide import ALLOW, ASK, BLOCK, LoadedContract, decide_call, load_contract
from prout_mcp.framing import read_message, write_message
from prout_mcp.server import initialize_result, protocol_version, rpc_error, rpc_ok


_TIMEOUT = 5.0
_GUARD_INSTRUCTIONS = (
    "This proxy checks every tools/call against a PROUT contract before the upstream server sees it. "
    "ALLOW is forwarded. BLOCK is returned as an error and is not forwarded. "
    "ASK is not forwarded unless PROUT_APPROVAL_TOKEN or --approval-token is set. "
    "The checker does not send mail or move money."
)


class Upstream:
    """One upstream MCP process, spoken to with newline-delimited JSON-RPC."""

    def __init__(self, command: list[str]):
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.queue: queue.Queue[Any] = queue.Queue()
        self._next_id = 1
        self._stdout_thread = threading.Thread(target=self._read_stdout, name="prout-guard-stdout", daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, name="prout-guard-stderr", daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            while True:
                message = read_message(self.proc.stdout)
                if message is None:
                    self.queue.put(None)
                    return
                self.queue.put(message)
        except ValueError as ex:
            self.queue.put(ex)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        while True:
            line = self.proc.stderr.readline()
            if line == b"":
                return
            sys.stderr.buffer.write(line)
            sys.stderr.buffer.flush()

    def request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        msg_id = self._next_id
        self._next_id += 1
        assert self.proc.stdin is not None
        write_message(
            self.proc.stdin,
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params},
        )
        return self._wait(msg_id, timeout)

    def notify(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        assert self.proc.stdin is not None
        write_message(self.proc.stdin, payload)

    def _wait(self, msg_id: int, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("upstream MCP server timed out")
            try:
                item = self.queue.get(timeout=remaining)
            except queue.Empty as ex:
                raise TimeoutError("upstream MCP server timed out") from ex
            if item is None:
                raise ConnectionError("upstream MCP server closed stdout")
            if isinstance(item, Exception):
                raise item
            if item.get("id") == msg_id:
                return item

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def run_guard(
    *,
    contract: str,
    upstream: list[str],
    agent: str = "",
    allow_net: Optional[list[str]] = None,
    deny_net: Optional[list[str]] = None,
    approval_token: Optional[str] = None,
    stdin: Optional[BinaryIO] = None,
    stdout: Optional[BinaryIO] = None,
    timeout: float = _TIMEOUT,
) -> int:
    """Proxy ``stdin``/``stdout`` to ``upstream``. Returns a process exit code."""
    if not upstream:
        print("[ERROR] guard needs an upstream command after --", file=sys.stderr)
        return 2
    try:
        source = Path(contract).read_text(encoding="utf-8")
    except OSError as ex:
        print(f"[ERROR] {ex}", file=sys.stderr)
        return 2
    loaded = load_contract(source, allow_net=allow_net, deny_net=deny_net)
    token = approval_token if approval_token is not None else os.environ.get("PROUT_APPROVAL_TOKEN", "")
    approved = bool(isinstance(token, str) and token.strip())
    incoming = stdin if stdin is not None else sys.stdin.buffer
    outgoing = stdout if stdout is not None else sys.stdout.buffer
    try:
        peer = Upstream(upstream)
    except OSError as ex:
        print(f"[ERROR] {ex}", file=sys.stderr)
        return 2
    try:
        _initialize_upstream(peer, timeout)
        _serve(incoming, outgoing, peer, loaded, agent=agent, approved=approved, timeout=timeout)
    finally:
        peer.close()
    return 0


def _initialize_upstream(peer: Upstream, timeout: float) -> None:
    try:
        peer.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "prout-guard", "version": __version__},
            },
            timeout,
        )
        peer.notify("notifications/initialized")
    except (TimeoutError, ConnectionError, ValueError, OSError) as ex:
        log_decision(
            {
                "audit_id": "",
                "tool": "",
                "verdict": BLOCK,
                "reason": f"upstream MCP server did not initialize: {ex}",
                "forwarded": False,
                "approved": False,
            }
        )


def _serve(
    incoming: BinaryIO,
    outgoing: BinaryIO,
    peer: Upstream,
    loaded: LoadedContract,
    *,
    agent: str,
    approved: bool,
    timeout: float,
) -> None:
    while True:
        try:
            message = read_message(incoming)
        except ValueError as ex:
            write_message(outgoing, rpc_error(None, -32700, str(ex)))
            continue
        if message is None:
            return
        try:
            response = handle_client(
                message,
                peer,
                loaded,
                agent=agent,
                approved=approved,
                timeout=timeout,
            )
        except BrokenPipeError:
            return
        if response is not None:
            try:
                write_message(outgoing, response)
            except BrokenPipeError:
                return


def handle_client(
    message: dict[str, Any],
    peer: Upstream,
    loaded: LoadedContract,
    *,
    agent: str,
    approved: bool,
    timeout: float,
) -> Optional[dict[str, Any]]:
    method = message.get("method")
    has_id = "id" in message
    params = message.get("params", {})
    if params is None:
        params = {}
    if not isinstance(method, str):
        if has_id:
            return rpc_error(message.get("id"), -32600, "invalid request")
        return None
    if not isinstance(params, dict):
        if has_id:
            return rpc_error(message["id"], -32602, "params must be an object")
        return None
    if method == "initialize":
        if not has_id:
            return None
        result = initialize_result(params)
        result["serverInfo"] = {"name": "prout-guard", "version": __version__}
        result["instructions"] = _GUARD_INSTRUCTIONS
        result["protocolVersion"] = protocol_version(params)
        return rpc_ok(message["id"], result)
    if method in {"notifications/initialized", "initialized"}:
        return None
    if method == "ping":
        return rpc_ok(message["id"], {}) if has_id else None
    if method == "tools/call":
        if not has_id:
            return None
        return guard_call(message["id"], params, peer, loaded, agent=agent, approved=approved, timeout=timeout)
    if not has_id:
        try:
            peer.notify(method, params)
        except OSError:
            return None
        return None
    return forward(message["id"], method, params, peer, timeout)


def guard_call(
    msg_id: Any,
    params: dict[str, Any],
    peer: Upstream,
    loaded: LoadedContract,
    *,
    agent: str,
    approved: bool,
    timeout: float,
) -> dict[str, Any]:
    name = params.get("name", "")
    if not isinstance(name, str):
        name = ""
    decision = decide_call(loaded, name.strip(), agent=agent)
    token_used = bool(approved and decision.verdict == ASK)
    forward_call = decision.verdict == ALLOW or token_used
    log_decision(
        {
            "audit_id": loaded.audit_id,
            "tool": decision.tool,
            "verdict": decision.verdict,
            "reason": decision.reason,
            "forwarded": forward_call,
            "approved": token_used,
        }
    )
    if not forward_call:
        return rpc_ok(msg_id, decision_result(decision.verdict, decision.tool, decision.reason, loaded.audit_id))
    return forward(msg_id, "tools/call", params, peer, timeout)


def decision_result(verdict: str, tool: str, reason: str, audit_id: str) -> dict[str, Any]:
    """Tool result for a call that was not forwarded."""
    asking = verdict == ASK
    if asking:
        text = (
            f"ASK: human approval required before calling {tool}. {reason} "
            "No upstream call was made. Set PROUT_APPROVAL_TOKEN or pass --approval-token to forward this call."
        )
    else:
        text = f"BLOCK: {reason}"
    payload = {
        "verdict": verdict if verdict in {ALLOW, BLOCK, ASK} else BLOCK,
        "tool": tool,
        "reason": reason,
        "audit_id": audit_id,
        "forwarded": False,
        "approval_required": asking,
    }
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": not asking,
    }


def forward(msg_id: Any, method: str, params: dict[str, Any], peer: Upstream, timeout: float) -> dict[str, Any]:
    try:
        upstream = peer.request(method, params, timeout)
    except (TimeoutError, ConnectionError, ValueError, OSError) as ex:
        return rpc_error(msg_id, -32000, f"upstream MCP server: {ex}")
    if "result" in upstream:
        result = upstream["result"]
        if not isinstance(result, dict):
            return rpc_error(msg_id, -32000, "upstream MCP server returned a result that is not an object")
        return rpc_ok(msg_id, result)
    error = upstream.get("error")
    if isinstance(error, dict):
        code = error.get("code", -32000)
        message = error.get("message", "upstream error")
        if not isinstance(code, int):
            code = -32000
        if not isinstance(message, str):
            message = "upstream error"
        return rpc_error(msg_id, code, message)
    return rpc_error(msg_id, -32000, "upstream MCP server returned no result")


def log_decision(record: dict[str, Any]) -> None:
    """Write one audit record to stderr. The token is never included."""
    write_message(sys.stderr.buffer, record)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="prout guard")
    parser.add_argument("--contract", required=True, help="PROUT contract file")
    parser.add_argument("--agent", default="", help="agent allow list to use when the contract declares several")
    parser.add_argument("--allow-net", action="append", default=None, help="replace the default net allow list (stripe)")
    parser.add_argument("--deny-net", action="append", default=None)
    parser.add_argument(
        "--approval-token",
        default=None,
        help="forward ASK calls when this value is non-empty; otherwise PROUT_APPROVAL_TOKEN is read",
    )
    parser.add_argument("upstream", nargs=argparse.REMAINDER, help="upstream command after --")
    args = parser.parse_args(argv)
    upstream = list(args.upstream)
    if upstream and upstream[0] == "--":
        upstream = upstream[1:]
    sys.exit(
        run_guard(
            contract=args.contract,
            upstream=upstream,
            agent=args.agent,
            allow_net=args.allow_net,
            deny_net=args.deny_net,
            approval_token=args.approval_token,
        )
    )
