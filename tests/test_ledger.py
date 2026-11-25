"""Runtime call counts and decimal spend caps.

The desk, the account, and the amounts are fictional.
"""
from __future__ import annotations

import json
import tempfile
import textwrap
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import support  # noqa: F401
from etsah import Checker, Parser
from etsah_mcp.approval import hash_arguments, sign_payload
from etsah_mcp.decide import load_contract
from etsah_mcp.ledger import reserve
from test_guard import GuardProcess, _audit

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "examples" / "agents"
KEY = "example-harbor-lane-key"
WHEN = datetime(2026, 11, 25, 1, 0, tzinfo=timezone.utc)


def _loaded(source: str):
    program = Parser(source).parse()
    return SimpleNamespace(program=program)


OP = """
op payments.charge(amount: Decimal) -> Bool
  eff spend(payments)
  spend from amount currency JPY
  limit calls 2 period day
  limit spend 10.00 JPY period day
  impl native
"""


class LedgerTests(unittest.TestCase):
    def test_spend_and_calls_persist_and_then_block(self) -> None:
        loaded = _loaded(OP)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "ledger.json")
            first = reserve(loaded, "payments.charge", {"amount": "4.00"}, path=path, now=WHEN)
            self.assertFalse(first.blocked)
            self.assertEqual(first.remaining_calls, 1)
            self.assertEqual(first.remaining_spend, "6.00")
            second = reserve(loaded, "payments.charge", {"amount": "4.00"}, path=path, now=WHEN)
            self.assertFalse(second.blocked)
            self.assertEqual(second.remaining_calls, 0)
            self.assertEqual(second.remaining_spend, "2.00")
            third = reserve(loaded, "payments.charge", {"amount": "1.00"}, path=path, now=WHEN)
            self.assertTrue(third.blocked)
            self.assertIn("call limit exceeded", third.reason)
            self.assertEqual(third.remaining_calls, 0)
            stored = Path(path).read_text(encoding="utf-8")
            self.assertIn("8.00", stored)
            self.assertNotIn(KEY, stored)

    def test_spend_cap_blocks_without_writing_the_overage(self) -> None:
        loaded = _loaded(OP)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            reserve(_loaded(OP), "payments.charge", {"amount": "6.00"}, path=str(path), now=WHEN)
            blocked = reserve(loaded, "payments.charge", {"amount": "5.00"}, path=str(path), now=WHEN)
            self.assertTrue(blocked.blocked)
            self.assertIn("spend limit exceeded", blocked.reason)
            self.assertEqual(blocked.remaining_spend, "4.00")
            used = json.loads(path.read_text(encoding="utf-8"))["used"]
            self.assertTrue(any(value == "6.00" for value in used.values()))

    def test_missing_amount_does_not_create_the_file(self) -> None:
        loaded = _loaded(OP)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            missing = reserve(loaded, "payments.charge", {}, path=str(path), now=WHEN)
            self.assertEqual(missing.reason, "spend amount cannot be read")
            self.assertFalse(path.exists())
            zero = reserve(loaded, "payments.charge", {"amount": "0.00"}, path=str(path), now=WHEN)
            self.assertEqual(zero.reason, "spend amount is not positive")
            self.assertFalse(path.exists())

    def test_rollback_returns_the_reservation(self) -> None:
        loaded = _loaded(OP)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "ledger.json")
            held = reserve(loaded, "payments.charge", {"amount": "4.00"}, path=path, now=WHEN)
            held.rollback()
            again = reserve(loaded, "payments.charge", {"amount": "10.00"}, path=path, now=WHEN)
            self.assertFalse(again.blocked)
            self.assertEqual(again.remaining_spend, "0.00")

    def test_two_writers_cannot_both_pass_a_cap_of_one(self) -> None:
        source = OP.replace("limit calls 2 period day", "limit calls 1 period day")
        source = source.replace("limit spend 10.00 JPY period day", "limit spend 10.00 JPY period day")
        loaded = _loaded(source)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "ledger.json")
            results: list[bool] = []
            barrier = threading.Barrier(2)

            def once() -> None:
                barrier.wait()
                result = reserve(loaded, "payments.charge", {"amount": "1.00"}, path=path, now=WHEN)
                results.append(result.blocked)

            threads = [threading.Thread(target=once) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sorted(results), [False, True])

    def test_a_new_day_starts_a_new_window(self) -> None:
        loaded = _loaded(OP)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "ledger.json")
            reserve(loaded, "payments.charge", {"amount": "10.00"}, path=path, now=WHEN)
            later = reserve(
                loaded,
                "payments.charge",
                {"amount": "10.00"},
                path=path,
                now=datetime(2026, 11, 26, 1, 0, tzinfo=timezone.utc),
            )
            self.assertFalse(later.blocked)
            self.assertEqual(later.remaining_spend, "0.00")


class StaticLimitTests(unittest.TestCase):
    def test_missing_decimal_cap_is_a_warning(self) -> None:
        source = """
        op payments.charge(amount: Decimal) -> Bool
          eff spend(payments)
          impl native
        agent clerk
          doc "charges"
          allow spend(payments)
          budget run 100 period 100
          limit net 0 hour
          limit emit 0 hour
          audit ast_hash
        policy
          payment spend(payments)
        gate approve_charge
          approver billing_lead
          before spend(payments)
          on_approved payments.charge
        task charge(amount: Decimal) -> Bool
          agent clerk
          doc "charge account A-19"
          trigger webhook "invoices"
          eff spend(payments)
          gate approve_charge
          key amount
          retry 0 escalate billing_lead
          let charged = payments.charge(amount)
          return charged
        """
        diags = Checker(Parser(textwrap.dedent(source)).parse()).check()
        warnings = [item for item in diags if item.severity == "warning"]
        self.assertTrue(any(item.code == "W-BUDGET" and "decimal amount limit" in item.message for item in warnings))
        self.assertFalse(any(item.severity == "error" for item in diags))

    def test_declared_cap_and_a_field_amount_are_quiet(self) -> None:
        diags = Checker(Parser((AGENTS / "invoice.etsah").read_text(encoding="utf-8")).parse()).check()
        self.assertEqual(diags, [])

    def test_currency_mismatch_and_a_task_over_the_agent_are_errors(self) -> None:
        source = """
        op payments.charge(amount: Decimal) -> Bool
          eff spend(payments)
          spend from amount currency JPY
          limit spend 20.00 USD period day
          impl native
        agent clerk
          doc "charges"
          allow spend(payments)
          budget run 100 period 100
          limit net 0 hour
          limit emit 0 hour
          limit spend 10.00 JPY period day
          audit ast_hash
        policy
          payment spend(payments)
        gate approve_charge
          approver billing_lead
          before spend(payments)
          on_approved payments.charge
        task charge(amount: Decimal) -> Bool
          agent clerk
          doc "charge account A-19"
          trigger webhook "invoices"
          eff spend(payments)
          gate approve_charge
          limit spend 30.00 JPY period day
          key amount
          retry 0 escalate billing_lead
          let charged = payments.charge(amount)
          return charged
        """
        diags = Checker(Parser(textwrap.dedent(source)).parse()).check()
        errors = [item.message for item in diags if item.severity == "error"]
        self.assertTrue(any("USD" in message and "JPY" in message for message in errors))
        self.assertTrue(any("exceeds the enclosing limit" in message for message in errors))

    def test_amount_field_must_be_decimal(self) -> None:
        source = """
        type Invoice {
          amount: String
        }
        op payments.charge(invoice: Invoice) -> Bool
          eff spend(payments)
          spend from invoice.amount currency JPY
          impl native
        """
        diags = Checker(Parser(textwrap.dedent(source)).parse()).check()
        self.assertTrue(any(item.code == "E-TYPE" and "Decimal" in item.message for item in diags))


class GuardLedgerTests(unittest.TestCase):
    def test_guard_blocks_over_the_cap_even_with_a_fresh_approval(self) -> None:
        loaded = load_contract((AGENTS / "invoice.etsah").read_text(encoding="utf-8"))
        arguments = {"invoice": {"amount": "4000.00"}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key_path = root / "approval.key"
            key_path.write_text(KEY + "\n", encoding="utf-8")
            nonce_path = root / "nonces.json"
            ledger_path = root / "ledger.json"
            first_token, first_payload = _charge_token(loaded.audit_id, arguments, "a" * 32, "b" * 32)
            log_path = root / "calls.jsonl"
            guard = GuardProcess(
                AGENTS / "invoice.etsah",
                log_path,
                extra=[
                    "--approval-key-file",
                    str(key_path),
                    "--nonce-file",
                    str(nonce_path),
                    "--ledger-file",
                    str(ledger_path),
                    "--approval-token",
                    first_token,
                ],
            )
            try:
                _init(guard)
                forwarded = guard.request("tools/call", {"name": "payments.charge", "arguments": arguments})
                self.assertEqual(forwarded["result"]["content"][0]["text"], "upstream:payments.charge")
            finally:
                stderr = guard.close()
            audit = [row for row in _audit(stderr) if row.get("tool") == "payments.charge"]
            self.assertTrue(audit[0]["forwarded"])
            self.assertEqual(audit[0]["remaining_spend"], "1000.00")
            self.assertEqual(audit[0]["spend_currency"], "JPY")
            self.assertEqual(audit[0]["remaining_calls"], 19)
            self.assertNotIn(KEY.encode(), stderr)

            second_token, second_payload = _charge_token(loaded.audit_id, arguments, "c" * 32, "d" * 32)
            second_log = root / "second.jsonl"
            second = GuardProcess(
                AGENTS / "invoice.etsah",
                second_log,
                extra=[
                    "--approval-key-file",
                    str(key_path),
                    "--nonce-file",
                    str(nonce_path),
                    "--ledger-file",
                    str(ledger_path),
                    "--approval-token",
                    second_token,
                ],
            )
            try:
                _init(second)
                blocked = second.request("tools/call", {"name": "payments.charge", "arguments": arguments})
                self.assertTrue(blocked["result"]["isError"])
                self.assertIn("spend limit exceeded", blocked["result"]["content"][0]["text"])
                self.assertEqual(blocked["result"]["structuredContent"]["remaining_spend"], "1000.00")
            finally:
                second.close()
            self.assertEqual(_logged(second_log), [])
            stored = nonce_path.read_text(encoding="utf-8")
            self.assertIn(first_payload["nonce"], stored)
            self.assertNotIn(second_payload["nonce"], stored)
            self.assertNotIn(KEY, stored)

    def test_unreadable_amount_does_not_spend_the_approval(self) -> None:
        loaded = load_contract((AGENTS / "invoice.etsah").read_text(encoding="utf-8"))
        arguments = {"invoice": {"amount": "100.00"}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key_path = root / "approval.key"
            key_path.write_text(KEY + "\n", encoding="utf-8")
            nonce_path = root / "nonces.json"
            ledger_path = root / "ledger.json"
            token, payload = _charge_token(loaded.audit_id, arguments, "e" * 32, "f" * 32)
            guard = GuardProcess(AGENTS / "invoice.etsah", root / "calls.jsonl", env_extra={
                "ETSAH_APPROVAL_KEY": KEY,
                "ETSAH_APPROVAL_NONCE_FILE": str(nonce_path),
                "ETSAH_LEDGER_FILE": str(ledger_path),
                "ETSAH_APPROVAL_TOKEN": token,
            })
            try:
                _init(guard)
                missing = guard.request("tools/call", {"name": "payments.charge", "arguments": {}})
                self.assertIn("spend amount cannot be read", missing["result"]["content"][0]["text"])
                forwarded = guard.request("tools/call", {"name": "payments.charge", "arguments": arguments})
                self.assertEqual(forwarded["result"]["content"][0]["text"], "upstream:payments.charge")
            finally:
                guard.close()
            stored = nonce_path.read_text(encoding="utf-8")
            self.assertIn(payload["nonce"], stored)


def _charge_token(audit_id: str, arguments: dict, approval_id: str, nonce: str) -> tuple[str, dict]:
    payload = {
        "v": 1,
        "approval_id": approval_id,
        "nonce": nonce,
        "approver_id": "billing_lead",
        "audit_id": audit_id,
        "gate": "approve_charge",
        "op": "payments.charge",
        "args_hash": hash_arguments(arguments),
        "exp": 1893456000,
    }
    return sign_payload(payload, KEY.encode("utf-8")), payload


def _init(guard: GuardProcess) -> None:
    guard.request(
        "initialize",
        {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "ledger-test", "version": "0"}},
    )


def _logged(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [json.loads(line)["tool"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
