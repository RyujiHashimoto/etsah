"""Stdio MCP proxy that checks each tools/call against a PROUT contract.

``prout guard --contract file.prout -- <upstream command>`` answers the MCP
client itself and forwards ``tools/list`` and allowed ``tools/call`` messages
to the upstream server. BLOCK is a tool result with ``isError`` true and is
never forwarded. ASK is forwarded only when ``PROUT_APPROVAL_TOKEN`` or
``--approval-token`` is a signed approval for this contract, gate, tool, and
argument hash, and the nonce has not been used. The shared key is read from
the environment or from a file and is not written to the log.

``--allow-legacy-approval-token`` (or ``PROUT_ALLOW_LEGACY_APPROVAL_TOKEN=1``)
restores the old presence check. That flag is deprecated.

Each decision is one JSON object on stderr, including ``audit_id``. A signed
approval also records ``approver_id`` and ``approval_id``.
"""
from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Optional

from prout_mcp import __version__
from prout_mcp.approval import (
    ApprovalError,
    Authorization,
    authorize,
    hash_arguments,
    legacy_enabled,
    load_key,
)
from prout_mcp.decide import ALLOW, ASK, BLOCK, LoadedContract, decide_call, gates_for_tool, load_contract
from prout_mcp.ledger import reserve
from prout_mcp.framing import read_message, write_message
from prout_mcp.server import initialize_result, protocol_version, rpc_error, rpc_ok


_TIMEOUT = 5.0
_GUARD_INSTRUCTIONS = (
    "This proxy checks every tools/call against a PROUT contract before the upstream server sees it. "
    "ALLOW is forwarded. BLOCK is returned as an error and is not forwarded. "
    "ASK is forwarded only for a signed, unexpired, unused approval of this contract, gate, tool, and arguments. "
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


@dataclass
class _ApprovalSetup:
    token: str
    legacy: bool
    key: Optional[bytes]
    key_error: str
    nonce_file: str


def run_guard(
    *,
    contract: str,
    upstream: list[str],
    agent: str = "",
    allow_net: Optional[list[str]] = None,
    deny_net: Optional[list[str]] = None,
    approval_token: Optional[str] = None,
    allow_legacy: bool = False,
    approval_key_file: Optional[str] = None,
    nonce_file: Optional[str] = None,
    ledger_file: Optional[str] = None,
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
    setup = _approval_setup(
        approval_token=approval_token,
        allow_legacy=allow_legacy,
        approval_key_file=approval_key_file,
        nonce_file=nonce_file,
    )
    incoming = stdin if stdin is not None else sys.stdin.buffer
    outgoing = stdout if stdout is not None else sys.stdout.buffer
    try:
        peer = Upstream(upstream)
    except OSError as ex:
        print(f"[ERROR] {ex}", file=sys.stderr)
        return 2
    try:
        _initialize_upstream(peer, timeout)
        ledger = ledger_file if ledger_file is not None else os.environ.get("PROUT_LEDGER_FILE", "")
        _serve(
            incoming,
            outgoing,
            peer,
            loaded,
            agent=agent,
            approval=setup,
            ledger_file=ledger.strip(),
            timeout=timeout,
        )
    finally:
        peer.close()
    return 0


def _approval_setup(
    *,
    approval_token: Optional[str],
    allow_legacy: bool,
    approval_key_file: Optional[str],
    nonce_file: Optional[str],
) -> _ApprovalSetup:
    if approval_token is None:
        token = os.environ.get("PROUT_APPROVAL_TOKEN", "")
    else:
        token = approval_token
    token = token.strip() if isinstance(token, str) else ""
    legacy = legacy_enabled(allow_legacy)
    store = nonce_file if nonce_file is not None else os.environ.get("PROUT_APPROVAL_NONCE_FILE", "")
    key: Optional[bytes] = None
    key_error = ""
    if token and not legacy:
        try:
            key = load_key(approval_key_file)
        except ApprovalError as ex:
            key_error = str(ex)
    return _ApprovalSetup(
        token=token,
        legacy=legacy,
        key=key,
        key_error=key_error,
        nonce_file=store.strip(),
    )


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
    approval: _ApprovalSetup,
    ledger_file: str,
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
                approval=approval,
                ledger_file=ledger_file,
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
    approval: _ApprovalSetup,
    ledger_file: str,
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
        return guard_call(
            message["id"],
            params,
            peer,
            loaded,
            agent=agent,
            approval=approval,
            ledger_file=ledger_file,
            timeout=timeout,
        )
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
    approval: _ApprovalSetup,
    ledger_file: str,
    timeout: float,
) -> dict[str, Any]:
    name = params.get("name", "")
    if not isinstance(name, str):
        name = ""
    arguments = params.get("arguments", {})
    if arguments is None:
        arguments = {}
    decision = decide_call(loaded, name.strip(), agent=agent)
    try:
        args_hash = hash_arguments(arguments)
    except ApprovalError:
        args_hash = ""
    reservation = None
    if decision.verdict != BLOCK:
        reservation = reserve(
            loaded,
            decision.tool,
            arguments,
            agent=agent,
            path=ledger_file,
        )
    if reservation is not None and reservation.blocked:
        record = _audit_record(
            loaded.audit_id,
            decision.tool,
            BLOCK,
            reservation.reason,
            forwarded=False,
            approved=False,
            args_hash=args_hash,
            remaining_calls=reservation.remaining_calls,
            remaining_spend=reservation.remaining_spend,
            spend_currency=reservation.currency,
        )
        log_decision(record)
        return rpc_ok(
            msg_id,
            decision_result(
                BLOCK,
                decision.tool,
                reservation.reason,
                loaded.audit_id,
                args_hash=args_hash,
                remaining_calls=reservation.remaining_calls,
                remaining_spend=reservation.remaining_spend,
                spend_currency=reservation.currency,
            ),
        )
    approver_id = ""
    approval_id = ""
    approval_error = ""
    approval_mode = ""
    token_used = False
    if decision.verdict == ASK and approval.token:
        if approval.legacy:
            token_used = True
            approval_mode = "legacy"
        else:
            granted = _authorize_ask(approval, loaded, decision.tool, arguments)
            approver_id = granted.approver_id
            approval_id = granted.approval_id
            if granted.accepted:
                token_used = True
                approval_mode = "signed"
            else:
                approval_error = granted.error
    forward_call = decision.verdict == ALLOW or token_used
    remaining_calls = None if reservation is None else reservation.remaining_calls
    remaining_spend = None if reservation is None else reservation.remaining_spend
    spend_currency = "" if reservation is None else reservation.currency
    if not forward_call and reservation is not None and reservation._rollback:
        reservation.rollback()
        remaining_calls = reservation.prior_calls
        remaining_spend = reservation.prior_spend
    record = _audit_record(
        loaded.audit_id,
        decision.tool,
        decision.verdict,
        decision.reason,
        forwarded=forward_call,
        approved=token_used,
        approver_id=approver_id,
        approval_id=approval_id,
        args_hash=args_hash,
        remaining_calls=remaining_calls,
        remaining_spend=remaining_spend,
        spend_currency=spend_currency,
        approval_mode=approval_mode,
        approval_error=approval_error,
    )
    log_decision(record)
    if not forward_call:
        return rpc_ok(
            msg_id,
            decision_result(
                decision.verdict,
                decision.tool,
                decision.reason,
                loaded.audit_id,
                args_hash=args_hash,
                approver_id=approver_id,
                approval_id=approval_id,
                approval_error=approval_error,
                remaining_calls=remaining_calls,
                remaining_spend=remaining_spend,
                spend_currency=spend_currency,
            ),
        )
    return forward(msg_id, "tools/call", params, peer, timeout)


def _audit_record(
    audit_id: str,
    tool: str,
    verdict: str,
    reason: str,
    *,
    forwarded: bool,
    approved: bool,
    args_hash: str,
    remaining_calls: Optional[int],
    remaining_spend: Optional[str],
    spend_currency: str,
    approver_id: str = "",
    approval_id: str = "",
    approval_mode: str = "",
    approval_error: str = "",
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "audit_id": audit_id,
        "tool": tool,
        "verdict": verdict,
        "reason": reason,
        "forwarded": forwarded,
        "approved": approved,
        "approver_id": approver_id,
        "approval_id": approval_id,
        "args_hash": args_hash,
        "remaining_calls": remaining_calls,
        "remaining_spend": remaining_spend,
        "spend_currency": spend_currency,
    }
    if approval_mode:
        record["approval_mode"] = approval_mode
    if approval_error:
        record["approval_error"] = approval_error
    return record


def _authorize_ask(approval: _ApprovalSetup, loaded: LoadedContract, tool: str, arguments: Any) -> Authorization:
    if approval.key_error:
        return Authorization(False, error=approval.key_error)
    return authorize(
        approval.token,
        key=approval.key,
        nonce_file=approval.nonce_file,
        audit_id=loaded.audit_id,
        op=tool,
        arguments=arguments,
        gates=gates_for_tool(loaded, tool),
    )


def decision_result(
    verdict: str,
    tool: str,
    reason: str,
    audit_id: str,
    *,
    args_hash: str = "",
    approver_id: str = "",
    approval_id: str = "",
    approval_error: str = "",
    remaining_calls: Optional[int] = None,
    remaining_spend: Optional[str] = None,
    spend_currency: str = "",
) -> dict[str, Any]:
    """Tool result for a call that was not forwarded."""
    asking = verdict == ASK
    if asking:
        text = (
            f"ASK: human approval required before calling {tool}. {reason} "
            "No upstream call was made. "
            "Forwarding requires a signed approval from `prout approve` for this contract, gate, tool, and arguments."
        )
        if approval_error:
            text += f" Approval was not accepted: {approval_error}."
    else:
        text = f"BLOCK: {reason}"
    payload = {
        "verdict": verdict if verdict in {ALLOW, BLOCK, ASK} else BLOCK,
        "tool": tool,
        "reason": reason,
        "audit_id": audit_id,
        "forwarded": False,
        "approval_required": asking,
        "approver_id": approver_id,
        "approval_id": approval_id,
        "args_hash": args_hash,
        "remaining_calls": remaining_calls,
        "remaining_spend": remaining_spend,
        "spend_currency": spend_currency,
    }
    if approval_error:
        payload["approval_error"] = approval_error
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
    """Write one audit record to stderr. The token and the shared key are never included."""
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
        help="signed approval for one ASK call; otherwise PROUT_APPROVAL_TOKEN is read. The key is not this value",
    )
    parser.add_argument(
        "--approval-key-file",
        default=None,
        help="shared HMAC key file. Otherwise PROUT_APPROVAL_KEY_FILE or PROUT_APPROVAL_KEY",
    )
    parser.add_argument(
        "--nonce-file",
        default=None,
        help="file that records used nonces. Otherwise PROUT_APPROVAL_NONCE_FILE",
    )
    parser.add_argument(
        "--allow-legacy-approval-token",
        action="store_true",
        help="deprecated: forward every ASK when the token is a non-empty string. BLOCK is still not forwarded",
    )
    parser.add_argument(
        "--ledger-file",
        default=None,
        help="file that stores call counts and spend. Otherwise PROUT_LEDGER_FILE",
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
            allow_legacy=args.allow_legacy_approval_token,
            approval_key_file=args.approval_key_file,
            nonce_file=args.nonce_file,
            ledger_file=args.ledger_file,
        )
    )
