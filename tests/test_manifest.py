"""The capability manifest on check --json.

The sample is the fictional staffing desk in examples/ai_tasks.etsah.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

import support  # noqa: F401
from etsah import Parser, ast_digest, ast_hash, capability_manifest

ROOT = Path(__file__).resolve().parents[1]
AI_TASKS = ROOT / "examples" / "ai_tasks.etsah"


def _check_json(path: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "etsah.py"), "check", "--json", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)


class CapabilityManifestTests(unittest.TestCase):
    def test_ai_tasks_manifest_is_stable(self) -> None:
        program = Parser(AI_TASKS.read_text(encoding="utf-8")).parse()
        expected = {
            "hash": "196df60c411e",
            "audit_id": "196df60c411e2ff94a690bc803dd4a5752c349f7baebd45033887cadc981eb38",
            "agents": [
                {
                    "name": "billing_clerk",
                    "allow": [["emit", "mail"], ["read", "timesheets"], ["write", "invoices"]],
                    "budget_run": 0,
                    "budget_period": 0,
                    "limit_net": 0,
                    "limit_emit": 10,
                    "gates": ["send_invoice"],
                },
                {
                    "name": "rental_mail",
                    "allow": [["emit", "mail"], ["read", "rentals"]],
                    "budget_run": 0,
                    "budget_period": 0,
                    "limit_net": 0,
                    "limit_emit": 30,
                    "gates": ["remind_return"],
                },
                {
                    "name": "staffing_clerk",
                    "allow": [["read", "contracts"], ["write", "queues"]],
                    "budget_run": 0,
                    "budget_period": 0,
                    "limit_net": 0,
                    "limit_emit": 0,
                    "gates": [],
                },
                {
                    "name": "staffing_mail",
                    "allow": [["emit", "mail"], ["read", "timesheets"]],
                    "budget_run": 0,
                    "budget_period": 0,
                    "limit_net": 0,
                    "limit_emit": 20,
                    "gates": ["remind_client"],
                },
            ],
            "signature": None,
        }
        self.assertEqual(ast_hash(program), expected["hash"])
        self.assertEqual(ast_digest(program), expected["audit_id"])
        self.assertEqual(capability_manifest(program), expected)
        first = _check_json(AI_TASKS)
        second = _check_json(AI_TASKS)
        self.assertEqual(first["hash"], expected["hash"])
        self.assertEqual(first["audit_id"], expected["audit_id"])
        self.assertEqual(first["manifest"], expected)
        self.assertEqual(json.dumps(first, ensure_ascii=False, indent=2), json.dumps(second, ensure_ascii=False, indent=2))
        self.assertIn("diagnostics", first)
        self.assertIn("plan", first)

    def test_manifest_omits_audit_id_when_there_are_no_tasks(self) -> None:
        program = Parser(
            "type Id\nop read_ticket(id: Id) -> Bool\n  doc \"read\"\n  eff read(tickets)\n  impl native\n"
        ).parse()
        manifest = capability_manifest(program)
        self.assertNotIn("audit_id", manifest)
        self.assertIsNone(manifest["signature"])
        self.assertEqual(manifest["agents"], [])
        self.assertEqual(manifest["hash"], ast_hash(program))
