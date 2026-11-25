"""Guard proxy tests. A fake upstream records the calls that were actually forwarded."""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import support  # noqa: F401
from etsah_api import check_file
from etsah_mcp.approval import hash_arguments, sign_payload
from etsah_mcp.decide import load_contract

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "examples" / "agents"
FAKE = ROOT / "tests" / "fake_mcp_upstream.py"
KEY = "example-harbor-lane-key"
_SECRET_ENV = (
    "ETSAH_APPROVAL_TOKEN",
    "ETSAH_APPROVAL_KEY",
    "ETSAH_APPROVAL_KEY_FILE",
    "ETSAH_APPROVAL_NONCE_FILE",
    "ETSAH_LEDGER_FILE",
    "ETSAH_ALLOW_LEGACY_APPROVAL_TOKEN",
    "PROUT_APPROVAL_TOKEN",
    "PROUT_APPROVAL_KEY",
    "PROUT_APPROVAL_KEY_FILE",
    "PROUT_APPROVAL_NONCE_FILE",
    "PROUT_LEDGER_FILE",
    "PROUT_ALLOW_LEGACY_APPROVAL_TOKEN",
)


def _approval_token(**overrides) -> tuple[str, dict]:
    loaded = load_contract((AGENTS / "support_reply.etsah").read_text(encoding="utf-8"))
    payload = {
        "v": 1,
        "approval_id": "a" * 32,
        "nonce": "b" * 32,
        "approver_id": "support_lead",
        "audit_id": loaded.audit_id,
        "gate": "approve_reply",
        "op": "mail.send_reply",
        "args_hash": hash_arguments({}),
        "exp": int(time.time()) + 600,
    }
    payload.update(overrides)
    return sign_payload(payload, KEY.encode("utf-8")), payload


class GuardProcess:
    def __init__(
        self,
        contract: Path,
        log_path: Path,
        extra: list[str] | None = None,
        token: str | None = None,
        env_extra: dict[str, str] | None = None,
    ):
        env = os.environ.copy()
        for name in _SECRET_ENV:
            env.pop(name, None)
        env["FAKE_MCP_LOG"] = str(log_path)
        if token is not None:
            env["ETSAH_APPROVAL_TOKEN"] = token
        if env_extra:
            env.update(env_extra)
        argv = [
            sys.executable,
            str(ROOT / "etsah.py"),
            "guard",
            "--contract",
            str(contract),
            *(extra or []),
            "--",
            sys.executable,
            str(FAKE),
        ]
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
            env=env,
        )
        self._next = 1

    def request(self, method: str, params: dict | None = None, timeout: float = 5) -> dict:
        msg_id = self._next
        self._next += 1
        payload: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
        self.proc.stdin.flush()
        assert self.proc.stdout is not None
        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not ready:
            err = b""
            if self.proc.stderr is not None:
                ready_err, _, _ = select.select([self.proc.stderr], [], [], 0.1)
                if ready_err:
                    err = self.proc.stderr.read1(8000)
            raise AssertionError(f"no guard response within {timeout}s; stderr={err!r}")
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("guard closed stdout")
        reply = json.loads(line.decode("utf-8"))
        if reply.get("id") != msg_id:
            raise AssertionError(f"expected id {msg_id}, got {reply}")
        return reply

    def close(self) -> bytes:
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2)
        err = b""
        if self.proc.stderr is not None:
            err = self.proc.stderr.read()
            self.proc.stderr.close()
        if self.proc.stdout is not None and not self.proc.stdout.closed:
            self.proc.stdout.close()
        return err


def _logged_tools(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [json.loads(line)["tool"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _audit(stderr: bytes) -> list[dict]:
    records = []
    for line in stderr.decode("utf-8").splitlines():
        if not line.startswith("{"):
            continue
        records.append(json.loads(line))
    return records


class GuardTests(unittest.TestCase):
    def test_allow_forwards_block_and_ask_do_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "calls.jsonl"
            guard = GuardProcess(AGENTS / "support_reply.etsah", log_path)
            try:
                init = guard.request(
                    "initialize",
                    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "guard-test", "version": "0"}},
                )
                self.assertEqual(init["result"]["serverInfo"]["name"], "etsah-guard")
                listed = guard.request("tools/list")
                names = [tool["name"] for tool in listed["result"]["tools"]]
                self.assertEqual(names, ["tickets.read", "mail.send_reply", "github.list_issues"])

                allowed = guard.request("tools/call", {"name": "tickets.read", "arguments": {"ticket_id": "T-1042"}})
                self.assertFalse(allowed["result"]["isError"])
                self.assertEqual(allowed["result"]["content"][0]["text"], "upstream:tickets.read")

                asked = guard.request("tools/call", {"name": "mail.send_reply", "arguments": {}})
                self.assertFalse(asked["result"]["isError"])
                self.assertIn("ASK:", asked["result"]["content"][0]["text"])
                self.assertEqual(asked["result"]["structuredContent"]["verdict"], "ASK")
                self.assertFalse(asked["result"]["structuredContent"]["forwarded"])
                self.assertIn("support_lead", asked["result"]["structuredContent"]["reason"])

                blocked = guard.request("tools/call", {"name": "payments.charge", "arguments": {}})
                self.assertTrue(blocked["result"]["isError"])
                self.assertIn("BLOCK:", blocked["result"]["content"][0]["text"])
                self.assertEqual(blocked["result"]["structuredContent"]["verdict"], "BLOCK")
            finally:
                stderr = guard.close()
            self.assertEqual(_logged_tools(log_path), ["tickets.read"])
            audit = [row for row in _audit(stderr) if row.get("tool")]
            self.assertEqual([row["verdict"] for row in audit], ["ALLOW", "ASK", "BLOCK"])
            self.assertTrue(all(row["audit_id"] == check_file(AGENTS / "support_reply.etsah").audit_id for row in audit))
            self.assertEqual([row["forwarded"] for row in audit], [True, False, False])
            self.assertNotIn(b"ETSAH_APPROVAL_TOKEN", stderr)

    def test_signed_approval_forwards_the_matching_ask_and_still_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "calls.jsonl"
            key_path = Path(tmp) / "approval.key"
            nonce_path = Path(tmp) / "nonces.json"
            key_path.write_text(KEY + "\n", encoding="utf-8")
            token, payload = _approval_token()
            guard = GuardProcess(
                AGENTS / "support_reply.etsah",
                log_path,
                extra=[
                    "--approval-key-file",
                    str(key_path),
                    "--nonce-file",
                    str(nonce_path),
                    "--approval-token",
                    token,
                ],
            )
            try:
                guard.request(
                    "initialize",
                    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "guard-test", "version": "0"}},
                )
                blocked = guard.request("tools/call", {"name": "payments.charge", "arguments": {}})
                self.assertTrue(blocked["result"]["isError"])
                self.assertIn("BLOCK:", blocked["result"]["content"][0]["text"])
                asked = guard.request("tools/call", {"name": "mail.send_reply", "arguments": {}})
                self.assertEqual(asked["result"]["content"][0]["text"], "upstream:mail.send_reply")
            finally:
                stderr = guard.close()
            self.assertEqual(_logged_tools(log_path), ["mail.send_reply"])
            audit = [row for row in _audit(stderr) if row.get("tool")]
            self.assertEqual(audit[0]["verdict"], "BLOCK")
            self.assertFalse(audit[0]["approved"])
            self.assertEqual(audit[0]["approver_id"], "")
            self.assertEqual(audit[0]["approval_id"], "")
            self.assertEqual(audit[1]["verdict"], "ASK")
            self.assertTrue(audit[1]["approved"])
            self.assertTrue(audit[1]["forwarded"])
            self.assertEqual(audit[1]["approver_id"], "support_lead")
            self.assertEqual(audit[1]["approval_id"], payload["approval_id"])
            self.assertEqual(audit[1]["approval_mode"], "signed")
            self.assertNotIn(token.encode("utf-8"), stderr)
            self.assertNotIn(KEY.encode("utf-8"), stderr)
            stored = nonce_path.read_text(encoding="utf-8")
            self.assertIn(payload["nonce"], stored)
            self.assertNotIn(KEY, stored)

    def test_plain_token_is_not_enough(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "calls.jsonl"
            key_path = Path(tmp) / "approval.key"
            nonce_path = Path(tmp) / "nonces.json"
            key_path.write_text(KEY + "\n", encoding="utf-8")
            guard = GuardProcess(
                AGENTS / "support_reply.etsah",
                log_path,
                extra=[
                    "--approval-key-file",
                    str(key_path),
                    "--nonce-file",
                    str(nonce_path),
                    "--approval-token",
                    "example-approval",
                ],
            )
            try:
                guard.request(
                    "initialize",
                    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "guard-test", "version": "0"}},
                )
                asked = guard.request("tools/call", {"name": "mail.send_reply", "arguments": {}})
                self.assertFalse(asked["result"]["structuredContent"]["forwarded"])
                self.assertIn("malformed approval", asked["result"]["content"][0]["text"])
            finally:
                stderr = guard.close()
            self.assertEqual(_logged_tools(log_path), [])
            audit = [row for row in _audit(stderr) if row.get("tool")]
            self.assertEqual(audit[0]["approval_error"], "malformed approval")
            self.assertEqual(audit[0]["approver_id"], "")
            self.assertFalse(audit[0]["approved"])
            self.assertNotIn(b"example-approval", stderr)
            self.assertNotIn(KEY.encode("utf-8"), stderr)

    def test_legacy_flag_forwards_ask_without_binding_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "calls.jsonl"
            guard = GuardProcess(
                AGENTS / "support_reply.etsah",
                log_path,
                extra=["--allow-legacy-approval-token", "--approval-token", "example-approval"],
            )
            try:
                guard.request(
                    "initialize",
                    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "guard-test", "version": "0"}},
                )
                first = guard.request("tools/call", {"name": "mail.send_reply", "arguments": {}})
                second = guard.request(
                    "tools/call",
                    {"name": "mail.send_reply", "arguments": {"body": "fictional"}},
                )
                blocked = guard.request("tools/call", {"name": "payments.charge", "arguments": {}})
                self.assertEqual(first["result"]["content"][0]["text"], "upstream:mail.send_reply")
                self.assertEqual(second["result"]["content"][0]["text"], "upstream:mail.send_reply")
                self.assertTrue(blocked["result"]["isError"])
            finally:
                stderr = guard.close()
            self.assertEqual(_logged_tools(log_path), ["mail.send_reply", "mail.send_reply"])
            audit = [row for row in _audit(stderr) if row.get("tool")]
            self.assertEqual(audit[0]["approval_mode"], "legacy")
            self.assertEqual(audit[0]["approver_id"], "")
            self.assertEqual(audit[0]["approval_id"], "")
            self.assertTrue(audit[1]["approved"])
            self.assertFalse(audit[2]["approved"])
            self.assertNotIn(b"example-approval", stderr)

    def test_reuse_wrong_contract_wrong_args_and_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key_path = root / "approval.key"
            key_path.write_text(KEY + "\n", encoding="utf-8")
            nonce_path = root / "nonces.json"
            token, payload = _approval_token()
            log_path = root / "reuse.jsonl"
            guard = GuardProcess(
                AGENTS / "support_reply.etsah",
                log_path,
                env_extra={
                    "ETSAH_APPROVAL_KEY": KEY,
                    "ETSAH_APPROVAL_NONCE_FILE": str(nonce_path),
                    "ETSAH_APPROVAL_TOKEN": token,
                },
            )
            try:
                guard.request(
                    "initialize",
                    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "guard-test", "version": "0"}},
                )
                mismatch = guard.request(
                    "tools/call",
                    {"name": "mail.send_reply", "arguments": {"body": "fictional"}},
                )
                self.assertIn("different arguments", mismatch["result"]["content"][0]["text"])
                self.assertEqual(mismatch["result"]["structuredContent"]["approval_id"], payload["approval_id"])
                forwarded = guard.request("tools/call", {"name": "mail.send_reply", "arguments": {}})
                self.assertEqual(forwarded["result"]["content"][0]["text"], "upstream:mail.send_reply")
                reused = guard.request("tools/call", {"name": "mail.send_reply", "arguments": {}})
                self.assertIn("already used", reused["result"]["content"][0]["text"])
            finally:
                stderr = guard.close()
            self.assertEqual(_logged_tools(log_path), ["mail.send_reply"])
            self.assertNotIn(token.encode("utf-8"), stderr)
            self.assertNotIn(KEY.encode("utf-8"), stderr)

            other, _ = _approval_token(audit_id="ab" * 32, nonce="c" * 32, approval_id="d" * 32)
            other_log = root / "other.jsonl"
            other_guard = GuardProcess(
                AGENTS / "support_reply.etsah",
                other_log,
                env_extra={
                    "ETSAH_APPROVAL_KEY": KEY,
                    "ETSAH_APPROVAL_NONCE_FILE": str(root / "other-nonces.json"),
                    "ETSAH_APPROVAL_TOKEN": other,
                },
            )
            try:
                other_guard.request(
                    "initialize",
                    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "guard-test", "version": "0"}},
                )
                rejected = other_guard.request("tools/call", {"name": "mail.send_reply", "arguments": {}})
                self.assertIn("different contract", rejected["result"]["content"][0]["text"])
            finally:
                other_guard.close()
            self.assertEqual(_logged_tools(other_log), [])

            expired, _ = _approval_token(exp=int(time.time()) - 10, nonce="e" * 32, approval_id="f" * 32)
            expired_log = root / "expired.jsonl"
            expired_guard = GuardProcess(
                AGENTS / "support_reply.etsah",
                expired_log,
                env_extra={
                    "ETSAH_APPROVAL_KEY": KEY,
                    "ETSAH_APPROVAL_NONCE_FILE": str(root / "expired-nonces.json"),
                    "ETSAH_APPROVAL_TOKEN": expired,
                },
            )
            try:
                expired_guard.request(
                    "initialize",
                    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "guard-test", "version": "0"}},
                )
                rejected = expired_guard.request("tools/call", {"name": "mail.send_reply", "arguments": {}})
                self.assertIn("approval expired", rejected["result"]["content"][0]["text"])
                again = expired_guard.request("tools/call", {"name": "mail.send_reply", "arguments": {}})
                self.assertIn("approval expired", again["result"]["content"][0]["text"])
            finally:
                expired_guard.close()
            self.assertEqual(_logged_tools(expired_log), [])
            self.assertFalse((root / "expired-nonces.json").exists())

            wrong_tool, _ = _approval_token(op="tickets.read", nonce="1" * 32, approval_id="2" * 32)
            wrong_gate, _ = _approval_token(gate="other_gate", nonce="3" * 32, approval_id="4" * 32)
            for label, bad in (("different tool", wrong_tool), ("different gate", wrong_gate)):
                bad_log = root / f"{label}.jsonl"
                bad_guard = GuardProcess(
                    AGENTS / "support_reply.etsah",
                    bad_log,
                    env_extra={
                        "ETSAH_APPROVAL_KEY": KEY,
                        "ETSAH_APPROVAL_NONCE_FILE": str(root / f"{label}-nonces.json"),
                        "ETSAH_APPROVAL_TOKEN": bad,
                    },
                )
                try:
                    bad_guard.request(
                        "initialize",
                        {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "guard-test", "version": "0"}},
                    )
                    rejected = bad_guard.request("tools/call", {"name": "mail.send_reply", "arguments": {}})
                    self.assertIn(label, rejected["result"]["content"][0]["text"])
                finally:
                    bad_guard.close()
                self.assertEqual(_logged_tools(bad_log), [])

    def test_net_allow_list_and_a_broken_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocked_log = Path(tmp) / "blocked.jsonl"
            blocked = GuardProcess(AGENTS / "github_triage.etsah", blocked_log)
            try:
                blocked.request(
                    "initialize",
                    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "guard-test", "version": "0"}},
                )
                reply = blocked.request("tools/call", {"name": "github.list_issues", "arguments": {}})
                self.assertTrue(reply["result"]["isError"])
                self.assertIn("github", reply["result"]["structuredContent"]["reason"])
            finally:
                blocked.close()
            self.assertEqual(_logged_tools(blocked_log), [])

            allowed_log = Path(tmp) / "allowed.jsonl"
            allowed = GuardProcess(
                AGENTS / "github_triage.etsah",
                allowed_log,
                extra=["--allow-net", "github"],
            )
            try:
                allowed.request(
                    "initialize",
                    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "guard-test", "version": "0"}},
                )
                reply = allowed.request("tools/call", {"name": "github.list_issues", "arguments": {}})
                self.assertEqual(reply["result"]["content"][0]["text"], "upstream:github.list_issues")
            finally:
                allowed.close()
            self.assertEqual(_logged_tools(allowed_log), ["github.list_issues"])

    def test_missing_contract_exits_before_serving(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "etsah.py"), "guard", "--contract", str(AGENTS / "missing.etsah"), "--", sys.executable, "-c", "print('no')"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("missing.etsah", proc.stderr)
        self.assertNotIn("no", proc.stdout)
        empty = subprocess.run(
            [sys.executable, str(ROOT / "etsah.py"), "guard", "--contract", str(AGENTS / "support_reply.etsah")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(empty.returncode, 2)
        self.assertIn("upstream", empty.stderr)

    def test_module_guard_entry_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "calls.jsonl"
            env = os.environ.copy()
            for name in _SECRET_ENV:
                env.pop(name, None)
            env["FAKE_MCP_LOG"] = str(log_path)
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "etsah_mcp",
                    "guard",
                    "--contract",
                    str(AGENTS / "support_reply.etsah"),
                    "--",
                    sys.executable,
                    str(FAKE),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=ROOT,
                env=env,
            )
            try:
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "m", "version": "0"}},
                        }
                    ).encode("utf-8")
                    + b"\n"
                )
                proc.stdin.flush()
                ready, _, _ = select.select([proc.stdout], [], [], 5)
                self.assertTrue(ready)
                reply = json.loads(proc.stdout.readline().decode("utf-8"))
                self.assertEqual(reply["result"]["serverInfo"]["name"], "etsah-guard")
            finally:
                if proc.stdin is not None and not proc.stdin.closed:
                    proc.stdin.close()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                if proc.stdout is not None:
                    proc.stdout.close()
                if proc.stderr is not None:
                    proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
