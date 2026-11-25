"""Prompt-injection demo: a leaking contract and a gated safe contract.

The inquiry, the vault token, and the refund are fictional.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
from etsah_api import check_file
from etsah_mcp.approval import hash_arguments, sign_payload
from test_guard import GuardProcess, _audit, _logged_tools

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
KEY = "example-harbor-lane-key"
SAFE = EXAMPLES / "prompt_injection_safe.etsah"
LEAK = EXAMPLES / "prompt_injection_leak.etsah"


class PromptInjectionTests(unittest.TestCase):
    def test_the_leak_is_rejected_and_the_safe_contract_is_quiet(self) -> None:
        leak = check_file(LEAK)
        self.assertFalse(leak.ok)
        self.assertTrue(any(item.code == "E-IFLOW-ARG" and "mail.forward" in item.message for item in leak.diagnostics))
        self.assertTrue(any("secret" in item.message for item in leak.diagnostics))
        safe = check_file(SAFE)
        self.assertTrue(safe.ok)
        self.assertEqual(safe.diagnostics, ())

    def test_guard_asks_for_mail_and_blocks_an_oversized_refund(self) -> None:
        checked = check_file(SAFE)
        small = {"amount": "100.00"}
        large = {"amount": "9000.00"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key_path = root / "approval.key"
            key_path.write_text(KEY + "\n", encoding="utf-8")
            nonce_path = root / "nonces.json"
            ledger_path = root / "ledger.json"
            small_token, small_payload = _refund_token(checked.audit_id, small, "a" * 32, "b" * 32)
            log_path = root / "calls.jsonl"
            guard = GuardProcess(
                SAFE,
                log_path,
                extra=[
                    "--approval-key-file",
                    str(key_path),
                    "--nonce-file",
                    str(nonce_path),
                    "--ledger-file",
                    str(ledger_path),
                    "--approval-token",
                    small_token,
                ],
            )
            try:
                guard.request(
                    "initialize",
                    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "injection-test", "version": "0"}},
                )
                blocked = guard.request("tools/call", {"name": "mail.forward", "arguments": {"note": "vault-token"}})
                self.assertTrue(blocked["result"]["isError"])
                self.assertEqual(blocked["result"]["structuredContent"]["verdict"], "BLOCK")
                read = guard.request("tools/call", {"name": "inbox.read", "arguments": {"id": "Q-4401"}})
                self.assertEqual(read["result"]["content"][0]["text"], "upstream:inbox.read")
                asked = guard.request(
                    "tools/call",
                    {"name": "mail.reply", "arguments": {"note": "Thanks, Harbor Lane Desk received inquiry Q-4401."}},
                )
                self.assertEqual(asked["result"]["structuredContent"]["verdict"], "ASK")
                self.assertFalse(asked["result"]["structuredContent"]["forwarded"])
                self.assertIn("desk_lead", asked["result"]["structuredContent"]["reason"])
                refunded = guard.request("tools/call", {"name": "payments.refund", "arguments": small})
                self.assertEqual(refunded["result"]["content"][0]["text"], "upstream:payments.refund")
            finally:
                stderr = guard.close()
            self.assertEqual(_logged_tools(log_path), ["inbox.read", "payments.refund"])
            audit = [row for row in _audit(stderr) if row.get("tool") == "payments.refund"]
            self.assertTrue(audit[0]["approved"])
            self.assertEqual(audit[0]["remaining_spend"], "900.00")
            self.assertEqual(audit[0]["spend_currency"], "JPY")
            self.assertNotIn(KEY.encode(), stderr)
            self.assertNotIn(small_token.encode(), stderr)

            large_token, large_payload = _refund_token(checked.audit_id, large, "c" * 32, "d" * 32)
            second_log = root / "large.jsonl"
            second = GuardProcess(
                SAFE,
                second_log,
                extra=[
                    "--approval-key-file",
                    str(key_path),
                    "--nonce-file",
                    str(nonce_path),
                    "--ledger-file",
                    str(ledger_path),
                    "--approval-token",
                    large_token,
                ],
            )
            try:
                second.request(
                    "initialize",
                    {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "injection-test", "version": "0"}},
                )
                over = second.request("tools/call", {"name": "payments.refund", "arguments": large})
                self.assertTrue(over["result"]["isError"])
                self.assertIn("spend limit exceeded", over["result"]["content"][0]["text"])
            finally:
                second.close()
            self.assertEqual(_logged_tools(second_log), [])
            stored = nonce_path.read_text(encoding="utf-8")
            self.assertIn(small_payload["nonce"], stored)
            self.assertNotIn(large_payload["nonce"], stored)
            self.assertNotIn(KEY, stored)


def _refund_token(audit_id: str, arguments: dict, approval_id: str, nonce: str) -> tuple[str, dict]:
    payload = {
        "v": 1,
        "approval_id": approval_id,
        "nonce": nonce,
        "approver_id": "desk_lead",
        "audit_id": audit_id,
        "gate": "approve_reply",
        "op": "payments.refund",
        "args_hash": hash_arguments(arguments),
        "exp": 1893456000,
    }
    return sign_payload(payload, KEY.encode("utf-8")), payload


if __name__ == "__main__":
    unittest.main()
