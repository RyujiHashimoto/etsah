"""Signed approvals: HMAC claims, expiry, and one-time nonces.

The shared key in these tests is fictional. It must not appear in an error
or in the nonce store.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import support  # noqa: F401
from prout_mcp.approval import (
    ApprovalError,
    authorize,
    hash_arguments,
    issue_approval,
    legacy_enabled,
    load_key,
    sign_payload,
    verify_token,
)
from prout_mcp.decide import gates_for_tool, load_contract

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "examples" / "agents"
KEY = "example-harbor-lane-key"
KEY_BYTES = KEY.encode("utf-8")


def _contract() -> str:
    return (AGENTS / "support_reply.prout").read_text(encoding="utf-8")


def _loaded():
    return load_contract(_contract())


def _payload(**overrides) -> dict:
    loaded = _loaded()
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
    return payload


def _token(**overrides) -> str:
    return sign_payload(_payload(**overrides), KEY_BYTES)


def _authorize(token: str, tmp: Path, **overrides):
    loaded = _loaded()
    arguments = overrides.pop("arguments", {})
    gates = overrides.pop("gates", gates_for_tool(loaded, overrides.get("op", "mail.send_reply")))
    return authorize(
        token,
        key=overrides.pop("key", KEY_BYTES),
        nonce_file=str(overrides.pop("nonce_file", tmp / "nonces.json")),
        audit_id=overrides.pop("audit_id", loaded.audit_id),
        op=overrides.pop("op", "mail.send_reply"),
        arguments=arguments,
        gates=gates,
        now=overrides.pop("now", None),
    )


class HashTests(unittest.TestCase):
    def test_canonical_json_ignores_key_order_and_spaces(self) -> None:
        left = hash_arguments({"b": 1, "a": {"d": 2, "c": "harbor-lane"}})
        right = hash_arguments(json.loads('{"a": {"c": "harbor-lane", "d": 2}, "b": 1}'))
        self.assertEqual(left, right)
        encoded = b'{"a":{"c":"harbor-lane","d":2},"b":1}'
        self.assertEqual(left, hashlib.sha256(encoded).hexdigest())

    def test_missing_arguments_hash_as_an_empty_object(self) -> None:
        self.assertEqual(hash_arguments(None), hash_arguments({}))
        self.assertEqual(hash_arguments({}), hashlib.sha256(b"{}").hexdigest())

    def test_different_values_differ(self) -> None:
        self.assertNotEqual(hash_arguments({}), hash_arguments({"ticket_id": "T-1042"}))

    def test_non_ascii_is_hashed_as_utf8(self) -> None:
        digest = hash_arguments({"desk": "窓口"})
        self.assertEqual(digest, hashlib.sha256('{"desk":"窓口"}'.encode("utf-8")).hexdigest())


class TokenTests(unittest.TestCase):
    def test_round_trip_and_a_bad_signature(self) -> None:
        token = _token()
        approval = verify_token(token, KEY_BYTES)
        self.assertEqual(approval.approver_id, "support_lead")
        self.assertEqual(approval.op, "mail.send_reply")
        flipped = token[:-1] + ("A" if token[-1] != "A" else "B")
        with self.assertRaises(ApprovalError) as caught:
            verify_token(flipped, KEY_BYTES)
        self.assertEqual(str(caught.exception), "bad signature")
        self.assertNotIn(KEY, str(caught.exception))

    def test_other_key_and_tampered_body_are_bad_signatures(self) -> None:
        token = _token()
        with self.assertRaises(ApprovalError) as caught:
            verify_token(token, b"other-example-key")
        self.assertEqual(str(caught.exception), "bad signature")
        payload, mac = token.split(".")[1:]
        mutated = "prout1." + ("A" if payload[0] != "A" else "B") + payload[1:] + "." + mac
        with self.assertRaises(ApprovalError) as caught:
            verify_token(mutated, KEY_BYTES)
        self.assertEqual(str(caught.exception), "bad signature")

    def test_malformed_tokens_are_rejected(self) -> None:
        for token in ("example-approval", "prout1.abc", "prout1.@@@.@@@", "", "prout1.."):
            with self.assertRaises(ApprovalError) as caught:
                verify_token(token, KEY_BYTES)
            self.assertEqual(str(caught.exception), "malformed approval")
            self.assertNotIn(KEY, str(caught.exception))

    def test_signed_noncanonical_json_is_malformed(self) -> None:
        body = b'{"v":1,"exp":' + str(int(time.time()) + 60).encode("ascii") + b'}'
        mac = hmac.new(KEY_BYTES, body, hashlib.sha256).digest()
        import base64

        token = "prout1." + base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")
        token += "." + base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")
        with self.assertRaises(ApprovalError) as caught:
            verify_token(token, KEY_BYTES)
        self.assertEqual(str(caught.exception), "malformed approval")

    def test_bool_expiry_cannot_be_signed(self) -> None:
        payload = _payload()
        payload["exp"] = True
        with self.assertRaises(ApprovalError) as caught:
            sign_payload(payload, KEY_BYTES)
        self.assertEqual(str(caught.exception), "malformed approval")


class AuthorizeTests(unittest.TestCase):
    def test_matching_call_is_accepted_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            token = _token()
            first = _authorize(token, path)
            self.assertTrue(first.accepted)
            self.assertEqual(first.approver_id, "support_lead")
            self.assertEqual(first.approval_id, "a" * 32)
            second = _authorize(token, path)
            self.assertFalse(second.accepted)
            self.assertEqual(second.error, "approval was already used")
            self.assertEqual(second.approval_id, "a" * 32)
            stored = (path / "nonces.json").read_text(encoding="utf-8")
            self.assertIn("b" * 32, stored)
            self.assertNotIn(KEY, stored)

    def test_reused_approval_id_is_rejected_even_with_a_new_nonce(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            self.assertTrue(_authorize(_token(), path).accepted)
            other = _token(nonce="c" * 32)
            again = _authorize(other, path)
            self.assertEqual(again.error, "approval was already used")

    def test_expiry_boundary_does_not_consume_the_nonce(self) -> None:
        exp = int(time.time()) + 60
        token = _token(exp=exp)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            fresh = _authorize(token, path, now=exp - 0.001)
            self.assertTrue(fresh.accepted)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            expired = _authorize(token, path, now=exp)
            self.assertEqual(expired.error, "approval expired")
            again = _authorize(token, path, now=exp + 5)
            self.assertEqual(again.error, "approval expired")
            self.assertFalse((path / "nonces.json").exists())

    def test_each_mismatch_is_rejected_without_consuming_the_nonce(self) -> None:
        loaded = _loaded()
        cases = {
            "approval is for a different contract": {"audit_id": "ab" * 32},
            "approval is for a different gate": {"gate": "other_gate"},
            "approval is for a different tool": {"op": "tickets.read"},
            "approval is for different arguments": {"arguments": {"body": "fictional"}},
            "approver does not match the gate": {"approver_id": "office_admin"},
        }
        for message, overrides in cases.items():
            with self.subTest(message=message):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp)
                    token = _token(**{key: value for key, value in overrides.items() if key != "arguments"})
                    rejected = _authorize(
                        token,
                        path,
                        arguments=overrides.get("arguments", {}),
                        op="mail.send_reply",
                        audit_id=loaded.audit_id,
                    )
                    self.assertEqual(rejected.error, message)
                    self.assertFalse((path / "nonces.json").exists())
                    accepted = _authorize(_token(nonce="d" * 32, approval_id="e" * 32), path)
                    self.assertTrue(accepted.accepted, message)

    def test_wrong_tool_does_not_burn_the_original_nonce(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            token = _token()
            rejected = _authorize(token, path, op="tickets.read", gates=gates_for_tool(_loaded(), "tickets.read"))
            self.assertEqual(rejected.error, "approval is for a different gate")
            accepted = _authorize(token, path)
            self.assertTrue(accepted.accepted)

    def test_missing_key_and_missing_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            token = _token()
            missing_key = _authorize(token, path, key=None)
            self.assertEqual(missing_key.error, "approval key is not configured")
            self.assertEqual(missing_key.approval_id, "")
            missing_store = _authorize(token, path, nonce_file="")
            self.assertEqual(missing_store.error, "nonce store is not configured")
            self.assertEqual(missing_store.approval_id, "a" * 32)

    def test_corrupt_store_is_left_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonces.json"
            path.write_text("{", encoding="utf-8")
            result = _authorize(_token(), Path(tmp), nonce_file=path)
            self.assertEqual(result.error, "nonce store is unreadable")
            self.assertEqual(path.read_text(encoding="utf-8"), "{")

    def test_two_writers_keep_both_nonces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonces.json"
            errors: list[str] = []
            barrier = threading.Barrier(8)

            def consume(index: int) -> None:
                token = _token(approval_id=f"{index:032x}", nonce=f"{index + 1:032x}")
                barrier.wait()
                result = _authorize(token, Path(tmp), nonce_file=path)
                if not result.accepted:
                    errors.append(result.error)

            threads = [threading.Thread(target=consume, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(stored["used_nonces"]), 8)
            self.assertNotIn(KEY, path.read_text(encoding="utf-8"))


class KeyTests(unittest.TestCase):
    def test_file_trailing_newline_and_bom_match_the_env_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approval.key"
            path.write_bytes(b"\xef\xbb\xbf" + KEY_BYTES + b"\n")
            self.assertEqual(load_key(str(path), {}), KEY_BYTES)
            self.assertEqual(load_key(None, {"PROUT_APPROVAL_KEY": KEY}), KEY_BYTES)

    def test_empty_multiline_and_two_sources_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.key"
            empty.write_text("\n", encoding="utf-8")
            with self.assertRaises(ApprovalError) as caught:
                load_key(str(empty), {})
            self.assertEqual(str(caught.exception), "approval key is empty")
            lines = Path(tmp) / "lines.key"
            lines.write_text(KEY + "\nmore\n", encoding="utf-8")
            with self.assertRaises(ApprovalError) as caught:
                load_key(str(lines), {})
            self.assertEqual(str(caught.exception), "approval key must be a single line")
            self.assertNotIn(KEY, str(caught.exception))
            with self.assertRaises(ApprovalError) as caught:
                load_key(str(empty), {"PROUT_APPROVAL_KEY": KEY})
            self.assertEqual(str(caught.exception), "set only one approval key source")
            self.assertNotIn(KEY, str(caught.exception))

    def test_binary_key_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approval.key"
            path.write_bytes(b"\xff\xfe" + KEY_BYTES)
            with self.assertRaises(ApprovalError) as caught:
                load_key(str(path), {})
            self.assertEqual(str(caught.exception), "approval key file is not text")
            self.assertNotIn(KEY, str(caught.exception))


class IssueTests(unittest.TestCase):
    def test_issue_binds_the_contract_gate_and_approver(self) -> None:
        loaded = _loaded()
        issued = issue_approval(
            contract=str(AGENTS / "support_reply.prout"),
            gate="approve_reply",
            op="mail.send_reply",
            approver_id="support_lead",
            arguments={"body": "fictional reply"},
            exp=int(time.time()) + 120,
            key=KEY_BYTES,
        )
        self.assertEqual(issued.audit_id, loaded.audit_id)
        self.assertEqual(issued.args_hash, hash_arguments({"body": "fictional reply"}))
        self.assertNotIn(KEY, issued.token)
        with tempfile.TemporaryDirectory() as tmp:
            accepted = _authorize(
                issued.token,
                Path(tmp),
                arguments={"body": "fictional reply"},
            )
            self.assertTrue(accepted.accepted)
            self.assertEqual(accepted.approval_id, issued.approval_id)

    def test_issue_refuses_a_broken_contract_and_a_wrong_approver(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            issue_approval(
                contract=str(AGENTS / "support_reply_broken.prout"),
                gate="approve_reply",
                op="mail.send_reply",
                approver_id="support_lead",
                arguments={},
                exp=int(time.time()) + 120,
                key=KEY_BYTES,
            )
        self.assertEqual(str(caught.exception), "the contract check failed")
        with self.assertRaises(ApprovalError) as caught:
            issue_approval(
                contract=str(AGENTS / "support_reply.prout"),
                gate="approve_reply",
                op="mail.send_reply",
                approver_id="office_admin",
                arguments={},
                exp=int(time.time()) + 120,
                key=KEY_BYTES,
            )
        self.assertEqual(str(caught.exception), "approver does not match the gate")
        with self.assertRaises(ApprovalError) as caught:
            issue_approval(
                contract=str(AGENTS / "support_reply.prout"),
                gate="approve_reply",
                op="payments.charge",
                approver_id="support_lead",
                arguments={},
                exp=int(time.time()) + 120,
                key=KEY_BYTES,
            )
        self.assertEqual(str(caught.exception), "tool is not declared as an op")

    def test_gates_for_the_sample_contract(self) -> None:
        loaded = _loaded()
        self.assertEqual(gates_for_tool(loaded, "mail.send_reply"), [("approve_reply", "support_lead")])
        self.assertEqual(gates_for_tool(loaded, "tickets.read"), [])


class LegacyFlagTests(unittest.TestCase):
    def test_only_the_explicit_value_enables_the_deprecated_check(self) -> None:
        self.assertFalse(legacy_enabled(False, {}))
        self.assertFalse(legacy_enabled(False, {"PROUT_ALLOW_LEGACY_APPROVAL_TOKEN": "true"}))
        self.assertFalse(legacy_enabled(False, {"PROUT_ALLOW_LEGACY_APPROVAL_TOKEN": "0"}))
        self.assertTrue(legacy_enabled(False, {"PROUT_ALLOW_LEGACY_APPROVAL_TOKEN": "1"}))
        self.assertTrue(legacy_enabled(True, {}))


class CliTests(unittest.TestCase):
    def _env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        for name in (
            "PROUT_APPROVAL_TOKEN",
            "PROUT_APPROVAL_KEY",
            "PROUT_APPROVAL_KEY_FILE",
            "PROUT_APPROVAL_NONCE_FILE",
            "PROUT_ALLOW_LEGACY_APPROVAL_TOKEN",
        ):
            env.pop(name, None)
        env.update(extra)
        return env

    def test_approve_prints_the_token_only_on_stdout(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "prout.py"),
                "approve",
                "--contract",
                str(AGENTS / "support_reply.prout"),
                "--gate",
                "approve_reply",
                "--op",
                "mail.send_reply",
                "--approver",
                "support_lead",
                "--args",
                "{}",
                "--ttl",
                "300",
            ],
            cwd=ROOT,
            env=self._env(PROUT_APPROVAL_KEY=KEY),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        token = proc.stdout.strip()
        self.assertTrue(token.startswith("prout1."))
        self.assertNotIn(token, proc.stderr)
        self.assertNotIn(KEY, proc.stderr)
        self.assertIn("approver_id: support_lead", proc.stderr)
        approval = verify_token(token, KEY_BYTES)
        self.assertEqual(approval.audit_id, _loaded().audit_id)
        self.assertEqual(approval.args_hash, hash_arguments({}))

    def test_json_output_and_a_rejected_approver(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "prout.py"),
                "approve",
                "--contract",
                str(AGENTS / "support_reply.prout"),
                "--gate",
                "approve_reply",
                "--op",
                "mail.send_reply",
                "--approver",
                "support_lead",
                "--args",
                '{"body":"fictional"}',
                "--ttl",
                "60",
                "--json",
            ],
            cwd=ROOT,
            env=self._env(PROUT_APPROVAL_KEY=KEY),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["op"], "mail.send_reply")
        self.assertNotIn(KEY, proc.stdout)
        self.assertNotIn(KEY, proc.stderr)
        rejected = subprocess.run(
            [
                sys.executable,
                str(ROOT / "prout.py"),
                "approve",
                "--contract",
                str(AGENTS / "support_reply.prout"),
                "--gate",
                "approve_reply",
                "--op",
                "mail.send_reply",
                "--approver",
                "office_admin",
                "--args",
                "{}",
                "--ttl",
                "60",
            ],
            cwd=ROOT,
            env=self._env(PROUT_APPROVAL_KEY=KEY),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("approver does not match the gate", rejected.stderr)
        self.assertNotIn(KEY, rejected.stderr)
        self.assertEqual(rejected.stdout, "")
