"""Guard proxy tests. A fake upstream records the calls that were actually forwarded."""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
from prout_api import check_file

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "examples" / "agents"
FAKE = ROOT / "tests" / "fake_mcp_upstream.py"


class GuardProcess:
    def __init__(self, contract: Path, log_path: Path, extra: list[str] | None = None, token: str | None = None):
        env = os.environ.copy()
        env.pop("PROUT_APPROVAL_TOKEN", None)
        env["FAKE_MCP_LOG"] = str(log_path)
        if token is not None:
            env["PROUT_APPROVAL_TOKEN"] = token
        argv = [
            sys.executable,
            str(ROOT / "prout.py"),
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
            guard = GuardProcess(AGENTS / "support_reply.prout", log_path)
            try:
                init = guard.request(
                    "initialize",
                    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "guard-test", "version": "0"}},
                )
                self.assertEqual(init["result"]["serverInfo"]["name"], "prout-guard")
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
            self.assertTrue(all(row["audit_id"] == check_file(AGENTS / "support_reply.prout").audit_id for row in audit))
            self.assertEqual([row["forwarded"] for row in audit], [True, False, False])
            self.assertNotIn(b"PROUT_APPROVAL_TOKEN", stderr)

    def test_approval_token_forwards_ask_and_still_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "calls.jsonl"
            guard = GuardProcess(
                AGENTS / "support_reply.prout",
                log_path,
                extra=["--approval-token", "example-approval"],
            )
            try:
                guard.request(
                    "initialize",
                    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "guard-test", "version": "0"}},
                )
                asked = guard.request("tools/call", {"name": "mail.send_reply", "arguments": {}})
                self.assertEqual(asked["result"]["content"][0]["text"], "upstream:mail.send_reply")
                blocked = guard.request("tools/call", {"name": "payments.charge", "arguments": {}})
                self.assertTrue(blocked["result"]["isError"])
                self.assertIn("BLOCK:", blocked["result"]["content"][0]["text"])
            finally:
                stderr = guard.close()
            self.assertEqual(_logged_tools(log_path), ["mail.send_reply"])
            audit = [row for row in _audit(stderr) if row.get("tool")]
            self.assertEqual(audit[0]["verdict"], "ASK")
            self.assertTrue(audit[0]["approved"])
            self.assertTrue(audit[0]["forwarded"])
            self.assertNotIn(b"example-approval", stderr)

    def test_net_allow_list_and_a_broken_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocked_log = Path(tmp) / "blocked.jsonl"
            blocked = GuardProcess(AGENTS / "github_triage.prout", blocked_log)
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
                AGENTS / "github_triage.prout",
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
            [sys.executable, str(ROOT / "prout.py"), "guard", "--contract", str(AGENTS / "missing.prout"), "--", sys.executable, "-c", "print('no')"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("missing.prout", proc.stderr)
        self.assertNotIn("no", proc.stdout)
        empty = subprocess.run(
            [sys.executable, str(ROOT / "prout.py"), "guard", "--contract", str(AGENTS / "support_reply.prout")],
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
            env.pop("PROUT_APPROVAL_TOKEN", None)
            env["FAKE_MCP_LOG"] = str(log_path)
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "prout_mcp",
                    "guard",
                    "--contract",
                    str(AGENTS / "support_reply.prout"),
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
                self.assertEqual(reply["result"]["serverInfo"]["name"], "prout-guard")
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
