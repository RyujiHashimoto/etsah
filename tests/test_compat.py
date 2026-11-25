"""Old names keep working for one minor version."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import support  # noqa: F401
import etsah
import etsah_api
import etsah_import
import etsah_mcp
import etsah_mcp.decide as etsah_decide
import etsahc
import prout
import prout_api
import prout_import
import prout_mcp
import prout_mcp.decide as prout_decide
import proutc
from etsah_api import check_file
from etsah_mcp.approval import (
    env_value,
    legacy_enabled,
    load_key,
    sign_payload,
    verify_token,
)
from etsah_mcp.server import call_tool

ROOT = Path(__file__).resolve().parents[1]
KEY = "example-harbor-lane-key"


class ShimTests(unittest.TestCase):
    def test_import_prout_reexports_etsah(self) -> None:
        self.assertIs(prout.Parser, etsah.Parser)
        self.assertIs(prout.Checker, etsah.Checker)
        self.assertIs(prout.ProutError, etsah.ProutError)
        self.assertIs(prout_api.check_file, etsah_api.check_file)
        self.assertIs(prout_import.import_mcp, etsah_import.import_mcp)
        self.assertEqual(prout_mcp.__version__, etsah_mcp.__version__)
        self.assertIs(prout_decide.load_contract, etsah_decide.load_contract)
        self.assertIs(proutc.parse_app, etsahc.parse_app)

    def test_prout_command_warns_and_checks(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "prout.py"), "check", str(ROOT / "examples" / "refund_fixed.etsah")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("新名称は etsah", proc.stderr)
        self.assertIn("Etsah check:", proc.stdout)
        self.assertNotIn("新名称は etsah", proc.stdout)

    def test_etsah_command_does_not_warn(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "etsah.py"), "check", str(ROOT / "examples" / "refund_fixed.etsah")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("deprecated", proc.stderr)
        self.assertNotIn("新名称は etsah", proc.stderr)

    def test_proutc_script_warns(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "patterns" / "proutc.py"), "find", "approval"],
            cwd=ROOT / "patterns",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("新名称は etsah", proc.stderr)
        self.assertIn("approval", proc.stdout)

    def test_prout_mcp_help_warns(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        proc = subprocess.run(
            [sys.executable, "-m", "prout_mcp", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("新名称は etsah", proc.stderr)
        self.assertIn("etsah-mcp", proc.stdout)

    def test_dot_prout_file_still_checks(self) -> None:
        source = (ROOT / "examples" / "refund_fixed.etsah").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refund_fixed.prout"
            path.write_text(source, encoding="utf-8")
            checked = check_file(path)
            self.assertTrue(checked.ok)
            proc = subprocess.run(
                [sys.executable, str(ROOT / "etsah.py"), "check", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("refund_fixed.prout", proc.stdout)


class WireCompatTests(unittest.TestCase):
    def test_old_env_names_still_configure_the_key(self) -> None:
        self.assertEqual(load_key(None, {"PROUT_APPROVAL_KEY": KEY}), KEY.encode("utf-8"))
        self.assertEqual(env_value({"PROUT_LEDGER_FILE": "ledger.json"}, "LEDGER_FILE"), "ledger.json")
        self.assertEqual(
            env_value({"ETSAH_LEDGER_FILE": "new.json", "PROUT_LEDGER_FILE": "old.json"}, "LEDGER_FILE"),
            "new.json",
        )
        self.assertTrue(legacy_enabled(False, {"PROUT_ALLOW_LEGACY_APPROVAL_TOKEN": "1"}))

    def test_prout1_token_still_verifies(self) -> None:
        token = sign_payload(
            {
                "v": 1,
                "approval_id": "a" * 32,
                "nonce": "b" * 32,
                "approver_id": "support_lead",
                "audit_id": "c" * 64,
                "gate": "approve_reply",
                "op": "mail.send_reply",
                "args_hash": "d" * 64,
                "exp": 4_102_444_800,
            },
            KEY.encode("utf-8"),
        )
        self.assertTrue(token.startswith("etsah1."))
        old = "prout1." + token.split(".", 1)[1]
        verified = verify_token(old, KEY.encode("utf-8"))
        self.assertEqual(verified.gate, "approve_reply")
        self.assertEqual(verified.op, "mail.send_reply")

    def test_old_mcp_tool_name_still_checks(self) -> None:
        source = (ROOT / "examples" / "agents" / "support_reply.etsah").read_text(encoding="utf-8")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = call_tool({"name": "prout_check", "arguments": {"source": source}})
        self.assertFalse(result["isError"])
        self.assertIn("prout_check is deprecated. 新名称は etsah", stderr.getvalue())
