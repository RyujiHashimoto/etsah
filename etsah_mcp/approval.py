"""Signed, one-time approvals for a single ASK tool call.

A token is ``etsah1.<payload>.<mac>``. ``prout1.`` still verifies for one minor version. The payload is canonical JSON. The mac
is HMAC-SHA256 over those bytes. The shared key comes from the environment
or from a file. It is never written to a log, and it is not accepted on the
command line.

The payload names who approved, which contract (``audit_id``), which gate,
which op, the SHA-256 of the arguments, an expiry, an approval id, and a
nonce. The guard records the nonce and the approval id when it forwards the
call. A second use of either is rejected. BLOCK is not decided here.

Author: Ryuji Hashimoto (J-PROUT Inc.)
"""
from __future__ import annotations

import argparse
import base64
import binascii
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from etsah_mcp.decide import gates_for_tool, load_contract


_MAX_TTL = 366 * 24 * 60 * 60
_MAX_TOKEN = 4096
_MAX_KEY = 4096
_FIELDS = ("approval_id", "approver_id", "args_hash", "audit_id", "exp", "gate", "nonce", "op", "v")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_OP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_B64_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ApprovalError(Exception):
    """A token, key, or nonce store that the guard must not treat as approval."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Approval:
    approval_id: str
    nonce: str
    approver_id: str
    audit_id: str
    gate: str
    op: str
    args_hash: str
    exp: int


@dataclass(frozen=True)
class IssuedApproval:
    token: str
    approval_id: str
    nonce: str
    approver_id: str
    audit_id: str
    gate: str
    op: str
    args_hash: str
    exp: int


@dataclass(frozen=True)
class Authorization:
    """Result of checking one token against one call. Ids are set only after the HMAC matches."""

    accepted: bool
    approver_id: str = ""
    approval_id: str = ""
    error: str = ""
    verified: bool = False


def hash_arguments(arguments: Any) -> str:
    """SHA-256 of canonical JSON. ``None`` is the empty object. Key order does not matter."""
    if arguments is None:
        arguments = {}
    try:
        encoded = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as ex:
        raise ApprovalError("arguments", "approval is for different arguments") from ex
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _env_values(env: Mapping[str, str], suffix: str) -> list[str]:
    """Values of ETSAH_<suffix>, then the deprecated PROUT_<suffix>, without duplicates."""
    found: list[str] = []
    for prefix in ("ETSAH_", "PROUT_"):
        key = prefix + suffix
        if key in env and env.get(key, "") != "":
            value = env.get(key, "")
            if value not in found:
                found.append(value)
    return found


def env_value(env: Mapping[str, str], suffix: str, default: str = "") -> str:
    """Read ETSAH_<suffix>. If it is unset, read PROUT_<suffix>."""
    found = _env_values(env, suffix)
    if not found:
        return default
    return found[0]


def load_key(key_file: Optional[str] = None, environ: Optional[Mapping[str, str]] = None) -> bytes:
    """Read the shared key from exactly one of the file flag, the file env, or the key env."""
    env = os.environ if environ is None else environ
    sources: list[tuple[str, str]] = []
    if key_file:
        sources.append(("file", key_file))
    for value in _env_values(env, "APPROVAL_KEY_FILE"):
        text = value.strip()
        if text and ("file", text) not in sources:
            sources.append(("file", text))
    for value in _env_values(env, "APPROVAL_KEY"):
        if ("env", value) not in sources:
            sources.append(("env", value))
    if not sources:
        raise ApprovalError("key", "approval key is not configured")
    if len(sources) > 1:
        raise ApprovalError("key", "set only one approval key source")
    kind, value = sources[0]
    if kind == "file":
        return _read_key_file(value)
    return _key_bytes(value)


def sign_payload(payload: Mapping[str, Any], key: bytes) -> str:
    """Sign a payload that already has the required fields. Expiry is not checked."""
    if not isinstance(key, (bytes, bytearray)) or len(key) == 0:
        raise ApprovalError("key", "approval key is not configured")
    body = _canonical_body(payload)
    mac = hmac.new(bytes(key), body, hashlib.sha256).digest()
    return "etsah1." + _b64e(body) + "." + _b64e(mac)


def verify_token(token: str, key: bytes) -> Approval:
    """Check the framing and the HMAC. Callers still have to bind the claims and the nonce."""
    if not isinstance(key, (bytes, bytearray)) or len(key) == 0:
        raise ApprovalError("key", "approval key is not configured")
    body, mac = _decode_token(token)
    expected = hmac.new(bytes(key), body, hashlib.sha256).digest()
    if len(mac) != len(expected) or not hmac.compare_digest(mac, expected):
        raise ApprovalError("bad_signature", "bad signature")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as ex:
        raise ApprovalError("malformed", "malformed approval") from ex
    if not isinstance(payload, dict):
        raise ApprovalError("malformed", "malformed approval")
    canonical = _canonical_body(payload)
    if len(canonical) != len(body) or not hmac.compare_digest(canonical, body):
        raise ApprovalError("malformed", "malformed approval")
    return _approval_from_payload(payload)


def authorize(
    token: str,
    *,
    key: Optional[bytes],
    nonce_file: str,
    audit_id: str,
    op: str,
    arguments: Any,
    gates: Sequence[tuple[str, str]],
    now: Optional[float] = None,
) -> Authorization:
    """Accept the token only when every claim matches this call and the nonce is new.

    Check order is signature, expiry, contract, gate, tool, arguments, approver,
    then the nonce store. A failed check does not record the nonce.
    """
    if not key:
        return Authorization(False, error="approval key is not configured")
    try:
        approval = verify_token(token, key)
    except ApprovalError as ex:
        return Authorization(False, error=str(ex))
    current = time.time() if now is None else now
    if current >= approval.exp:
        return _reject(approval, "approval expired")
    if approval.audit_id != audit_id:
        return _reject(approval, "approval is for a different contract")
    covering = [(name, approver) for name, approver in gates if name == approval.gate]
    if not covering:
        return _reject(approval, "approval is for a different gate")
    if approval.op != op:
        return _reject(approval, "approval is for a different tool")
    try:
        actual = hash_arguments(arguments)
    except ApprovalError:
        return _reject(approval, "approval is for different arguments")
    if approval.args_hash != actual:
        return _reject(approval, "approval is for different arguments")
    if approval.approver_id != covering[0][1]:
        return _reject(approval, "approver does not match the gate")
    if not nonce_file:
        return _reject(approval, "nonce store is not configured")
    try:
        consume_nonce(nonce_file, approval.nonce, approval.approval_id, approval.exp, current)
    except ApprovalError as ex:
        return _reject(approval, str(ex))
    return Authorization(
        True,
        approver_id=approval.approver_id,
        approval_id=approval.approval_id,
        verified=True,
    )


def consume_nonce(path: str, nonce: str, approval_id: str, exp: int, now: float) -> None:
    """Record ``nonce`` and ``approval_id``. A repeat of either is rejected."""
    store = Path(path)
    parent = store.parent
    if parent != Path("") and not parent.is_dir():
        raise ApprovalError("nonce_store", "nonce store is not writable")
    lock_path = Path(str(store) + ".lock")
    try:
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as ex:
        raise ApprovalError("nonce_store", "nonce store is not writable") from ex
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            data = _read_store(store)
            if nonce in data["used_nonces"] or approval_id in data["used_approval_ids"]:
                raise ApprovalError("reused", "approval was already used")
            data["used_nonces"][nonce] = {
                "approval_id": approval_id,
                "exp": exp,
                "used_at": int(now),
            }
            data["used_approval_ids"][approval_id] = nonce
            _write_store(store, data)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def issue_approval(
    *,
    contract: str,
    gate: str,
    op: str,
    approver_id: str,
    arguments: Any,
    exp: int,
    key: bytes,
    now: Optional[float] = None,
    approval_id: Optional[str] = None,
    nonce: Optional[str] = None,
) -> IssuedApproval:
    """Sign one approval for a clean contract. The nonce is recorded later, when the guard forwards."""
    current = time.time() if now is None else now
    if isinstance(exp, bool) or not isinstance(exp, int):
        raise ApprovalError("expired", "approval expiry must be a unix time")
    if exp <= current:
        raise ApprovalError("expired", "approval expiry is not in the future")
    if exp > current + _MAX_TTL:
        raise ApprovalError("expired", "approval expires too far in the future")
    if not isinstance(arguments, dict):
        raise ApprovalError("arguments", "arguments must be a JSON object")
    try:
        source = Path(contract).read_text(encoding="utf-8")
    except OSError as ex:
        raise ApprovalError("contract", "contract file could not be read") from ex
    loaded = load_contract(source)
    if loaded.parse_error or not loaded.ok:
        raise ApprovalError("contract", "the contract check failed")
    program = loaded.program
    ops = {item.name: item for item in program.ops}
    if op not in ops:
        raise ApprovalError("tool", "tool is not declared as an op")
    declared = {item.name: item for item in program.gates}
    if gate not in declared:
        raise ApprovalError("gate", "gate is not declared")
    covering = gates_for_tool(loaded, op)
    match = [item for item in covering if item[0] == gate]
    if not match:
        raise ApprovalError("gate", "the gate does not cover this tool")
    if approver_id != match[0][1]:
        raise ApprovalError("approver", "approver does not match the gate")
    approval_id = approval_id or secrets.token_hex(16)
    nonce = nonce or secrets.token_hex(16)
    payload = {
        "v": 1,
        "approval_id": approval_id,
        "nonce": nonce,
        "approver_id": approver_id,
        "audit_id": loaded.audit_id,
        "gate": gate,
        "op": op,
        "args_hash": hash_arguments(arguments),
        "exp": exp,
    }
    token = sign_payload(payload, key)
    return IssuedApproval(
        token=token,
        approval_id=approval_id,
        nonce=nonce,
        approver_id=approver_id,
        audit_id=loaded.audit_id,
        gate=gate,
        op=op,
        args_hash=payload["args_hash"],
        exp=exp,
    )


def legacy_enabled(flag: bool, environ: Optional[Mapping[str, str]] = None) -> bool:
    """The old non-empty token is off unless the flag or ``ETSAH_ALLOW_LEGACY_APPROVAL_TOKEN=1`` is set.

    ``PROUT_ALLOW_LEGACY_APPROVAL_TOKEN=1`` is still accepted for one minor version.
    """
    env = os.environ if environ is None else environ
    return bool(flag) or env.get("ETSAH_ALLOW_LEGACY_APPROVAL_TOKEN") == "1" or env.get("PROUT_ALLOW_LEGACY_APPROVAL_TOKEN") == "1"


def add_approve_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--contract", required=True, help="Etsah contract file. Its audit id is signed")
    parser.add_argument("--gate", required=True, help="gate that covers the tool")
    parser.add_argument("--op", required=True, help="tool name, such as mail.send_reply")
    parser.add_argument("--approver", required=True, help="approver id declared on the gate")
    parser.add_argument("--args", default=None, help="JSON object of tool arguments")
    parser.add_argument("--args-file", default=None, help="file containing the JSON arguments")
    parser.add_argument("--ttl", default=None, help="seconds until expiry, from the current unix second")
    parser.add_argument("--expires-at", default=None, help="expiry as a unix time")
    parser.add_argument("--approval-key-file", default=None, help="shared key file. Otherwise ETSAH_APPROVAL_KEY_FILE or ETSAH_APPROVAL_KEY. PROUT_* is still read")
    parser.add_argument("--json", action="store_true", help="print the claims and the token as one JSON object")


def run_approve(args: argparse.Namespace, environ: Optional[Mapping[str, str]] = None) -> int:
    """Issue one token. The token is written to stdout. The key is not written."""
    try:
        key = load_key(args.approval_key_file, environ)
        arguments = _load_arguments(args.args, args.args_file)
        exp = _expiry(args.ttl, args.expires_at, time.time())
        issued = issue_approval(
            contract=args.contract,
            gate=args.gate,
            op=args.op,
            approver_id=args.approver,
            arguments=arguments,
            exp=exp,
            key=key,
        )
    except ApprovalError as ex:
        print(f"[ERROR] {ex}", file=sys.stderr)
        return 2
    if args.json:
        payload = {
            "token": issued.token,
            "approval_id": issued.approval_id,
            "approver_id": issued.approver_id,
            "audit_id": issued.audit_id,
            "gate": issued.gate,
            "op": issued.op,
            "args_hash": issued.args_hash,
            "exp": issued.exp,
        }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        print(issued.token)
        print(f"approval_id: {issued.approval_id}", file=sys.stderr)
        print(f"approver_id: {issued.approver_id}", file=sys.stderr)
        print(f"audit_id: {issued.audit_id}", file=sys.stderr)
        print(f"gate: {issued.gate}", file=sys.stderr)
        print(f"op: {issued.op}", file=sys.stderr)
        print(f"args_hash: {issued.args_hash}", file=sys.stderr)
        print(f"exp: {issued.exp}", file=sys.stderr)
    return 0


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="etsah approve")
    add_approve_arguments(parser)
    args = parser.parse_args(argv)
    sys.exit(run_approve(args))


def _reject(approval: Approval, message: str) -> Authorization:
    return Authorization(
        False,
        approver_id=approval.approver_id,
        approval_id=approval.approval_id,
        error=message,
        verified=True,
    )


def _canonical_body(payload: Mapping[str, Any]) -> bytes:
    if not isinstance(payload, dict) or set(payload) != set(_FIELDS):
        raise ApprovalError("malformed", "malformed approval")
    version = payload["v"]
    exp = payload["exp"]
    if isinstance(version, bool) or version != 1:
        raise ApprovalError("malformed", "malformed approval")
    if isinstance(exp, bool) or not isinstance(exp, int) or exp <= 0 or exp >= 2**63:
        raise ApprovalError("malformed", "malformed approval")
    checks = {
        "approval_id": _ID_RE,
        "nonce": _ID_RE,
        "approver_id": _NAME_RE,
        "audit_id": _HEX64,
        "args_hash": _HEX64,
        "gate": _NAME_RE,
        "op": _OP_RE,
    }
    ordered: dict[str, Any] = {"exp": exp, "v": 1}
    for field, pattern in checks.items():
        value = payload[field]
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise ApprovalError("malformed", "malformed approval")
        ordered[field] = value
    return json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _approval_from_payload(payload: Mapping[str, Any]) -> Approval:
    return Approval(
        approval_id=str(payload["approval_id"]),
        nonce=str(payload["nonce"]),
        approver_id=str(payload["approver_id"]),
        audit_id=str(payload["audit_id"]),
        gate=str(payload["gate"]),
        op=str(payload["op"]),
        args_hash=str(payload["args_hash"]),
        exp=int(payload["exp"]),
    )


def _decode_token(token: str) -> tuple[bytes, bytes]:
    if not isinstance(token, str) or len(token) > _MAX_TOKEN or "\n" in token or "\r" in token:
        raise ApprovalError("malformed", "malformed approval")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] not in {"etsah1", "prout1"} or not parts[1] or not parts[2]:
        raise ApprovalError("malformed", "malformed approval")
    try:
        body = _b64d(parts[1])
        mac = _b64d(parts[2])
    except (ValueError, binascii.Error) as ex:
        raise ApprovalError("malformed", "malformed approval") from ex
    if not body or len(mac) != 32:
        raise ApprovalError("malformed", "malformed approval")
    return body, mac


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    if _B64_RE.fullmatch(text) is None:
        raise ValueError("approval encoding")
    pad = "=" * ((4 - len(text) % 4) % 4)
    raw = base64.urlsafe_b64decode(text + pad)
    if _b64e(raw) != text:
        raise ValueError("approval encoding")
    return raw


def _read_key_file(path: str) -> bytes:
    try:
        raw = Path(path).read_bytes()
    except OSError as ex:
        raise ApprovalError("key", "approval key file could not be read") from ex
    if len(raw) > _MAX_KEY + 4:
        raise ApprovalError("key", "approval key is too long")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as ex:
        raise ApprovalError("key", "approval key file is not text") from ex
    return _key_bytes(text)


def _key_bytes(text: str) -> bytes:
    if text.startswith("\ufeff"):
        text = text[1:]
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if text == "":
        raise ApprovalError("key", "approval key is empty")
    if "\n" in text or "\r" in text or "\x00" in text:
        raise ApprovalError("key", "approval key must be a single line")
    raw = text.encode("utf-8")
    if len(raw) > _MAX_KEY:
        raise ApprovalError("key", "approval key is too long")
    return raw


def _empty_store() -> dict[str, Any]:
    return {"used_approval_ids": {}, "used_nonces": {}}


def _read_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_store()
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text) if text.strip() else _empty_store()
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise ApprovalError("nonce_store", "nonce store is unreadable") from ex
    if not isinstance(payload, dict):
        raise ApprovalError("nonce_store", "nonce store is unreadable")
    nonces = payload.get("used_nonces")
    ids = payload.get("used_approval_ids")
    if not isinstance(nonces, dict) or not isinstance(ids, dict):
        raise ApprovalError("nonce_store", "nonce store is unreadable")
    if set(payload) - {"used_nonces", "used_approval_ids"}:
        raise ApprovalError("nonce_store", "nonce store is unreadable")
    return {"used_nonces": dict(nonces), "used_approval_ids": dict(ids)}


def _write_store(path: Path, data: Mapping[str, Any]) -> None:
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as ex:
        raise ApprovalError("nonce_store", "nonce store is not writable") from ex
    try:
        view = blob
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
    except OSError as ex:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise ApprovalError("nonce_store", "nonce store is not writable") from ex
    finally:
        os.close(fd)
    try:
        os.replace(tmp, path)
    except OSError as ex:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise ApprovalError("nonce_store", "nonce store is not writable") from ex
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load_arguments(text: Optional[str], path: Optional[str]) -> Any:
    if (text is None) == (path is None):
        raise ApprovalError("arguments", "pass exactly one of --args and --args-file")
    if path is not None:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as ex:
            raise ApprovalError("arguments", "arguments file could not be read") from ex
    assert text is not None
    try:
        return json.loads(text)
    except json.JSONDecodeError as ex:
        raise ApprovalError("arguments", "arguments are not JSON") from ex


def _expiry(ttl: Optional[str], expires_at: Optional[str], now: float) -> int:
    if (ttl is None) == (expires_at is None):
        raise ApprovalError("expired", "pass exactly one of --ttl and --expires-at")
    if ttl is not None:
        seconds = _whole_number(ttl, "ttl")
        if seconds < 1 or seconds > _MAX_TTL:
            raise ApprovalError("expired", "ttl is out of range")
        return int(now) + seconds
    stamp = _whole_number(expires_at or "", "expires-at")
    if stamp <= now:
        raise ApprovalError("expired", "approval expiry is not in the future")
    if stamp > now + _MAX_TTL:
        raise ApprovalError("expired", "approval expires too far in the future")
    return stamp


def _whole_number(text: str, label: str) -> int:
    if not re.fullmatch(r"[0-9]+", text):
        raise ApprovalError("expired", f"{label} must be a positive integer")
    return int(text)
