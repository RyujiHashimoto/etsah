"""Stable check_source / check_file wrapper. It does not reimplement the checker."""
from __future__ import annotations

import unittest
from pathlib import Path

import support  # noqa: F401
from prout_api import check_file, check_source

ROOT = Path(__file__).resolve().parents[1]

# Display hashes for programs that do not declare agent or task.
SAMPLE_HASHES = {
    "refund_broken.prout": "c0ee67d7cba1",
    "refund_fixed.prout": "bae8501e8b3f",
    "staffing.prout": "afd29e7ccdd0",
    "staffing_broken.prout": "afbf07d01bd2",
    "staffing_dates.prout": "ecc914507aae",
    "staffing_dates_broken.prout": "5a9f7cb9dd46",
}


class CheckSourceTests(unittest.TestCase):
    def test_parse_error_is_a_diagnostic_without_a_hash(self) -> None:
        result = check_source("op nope\n")
        self.assertFalse(result.ok)
        self.assertEqual(result.ast_hash, "")
        self.assertEqual(result.audit_id, "")
        self.assertEqual(result.plans, ())
        self.assertEqual(len(result.diagnostics), 1)
        diagnostic = result.diagnostics[0]
        self.assertEqual(diagnostic.code, "E-PARSE")
        self.assertGreater(diagnostic.line, 0)
        self.assertIn("bad op header", diagnostic.message)
        self.assertEqual(diagnostic.as_dict()["code"], "E-PARSE")

    def test_clean_task_returns_a_plan_and_the_display_hash(self) -> None:
        result = check_file(ROOT / "examples" / "agents" / "support_reply.prout")
        self.assertTrue(result.ok)
        self.assertEqual(result.diagnostics, ())
        self.assertEqual(len(result.ast_hash), 12)
        self.assertEqual(len(result.audit_id), 64)
        self.assertTrue(result.audit_id.startswith(result.ast_hash))
        self.assertEqual(result.path, str(ROOT / "examples" / "agents" / "support_reply.prout"))
        self.assertEqual(len(result.plans), 1)
        plan = result.plans[0]
        self.assertEqual(plan.name, "draft_reply")
        self.assertEqual(plan.agent, "support_reply")
        self.assertEqual(plan.effects, ("read(tickets)", "emit(mail)"))
        self.assertEqual(plan.gates, ("approve_reply",))
        self.assertIn("support_lead", plan.text)
        self.assertIn("webhook", plan.trigger)
        payload = result.as_dict()
        self.assertEqual(payload["plans"][0]["name"], "draft_reply")
        self.assertTrue(payload["ok"])

    def test_policy_failure_keeps_the_plan_and_names_the_line(self) -> None:
        result = check_file(ROOT / "examples" / "agents" / "support_reply_broken.prout")
        self.assertFalse(result.ok)
        errors = [item for item in result.diagnostics if item.severity == "error"]
        self.assertEqual([item.code for item in errors], ["E-POLICY"])
        self.assertIn("emit to an external party has no approval gate", errors[0].message)
        self.assertGreater(errors[0].line, 0)
        self.assertEqual(result.plans[0].name, "draft_reply")
        self.assertEqual(result.plans[0].gates, ())

    def test_allow_net_is_passed_through(self) -> None:
        path = ROOT / "examples" / "agents" / "github_triage.prout"
        blocked = check_file(path)
        self.assertFalse(blocked.ok)
        self.assertTrue(any("github" in item.message for item in blocked.diagnostics))
        allowed = check_file(path, allow_net=["github"])
        self.assertTrue(allowed.ok)
        self.assertEqual(allowed.plans[0].name, "triage_open_issues")
        self.assertIn("net 30/hour", allowed.plans[0].text)

    def test_deny_net_is_passed_through(self) -> None:
        result = check_source(
            'op ping() -> Bool\n  eff net(stripe)\n  impl native\n',
            deny_net=["stripe"],
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.diagnostics[0].code, "E-POLICY")
        self.assertIn("denied", result.diagnostics[0].message)

    def test_a_warning_stays_ok_and_keeps_the_sample_hash(self) -> None:
        result = check_file(ROOT / "examples" / "refund_fixed.prout")
        self.assertTrue(result.ok)
        self.assertEqual(result.ast_hash, "bae8501e8b3f")
        warnings = [item for item in result.diagnostics if item.severity == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].code, "W-PRE")
        self.assertIn("order.status", warnings[0].message)
        self.assertEqual(result.plans, ())

    def test_existing_samples_keep_their_hashes(self) -> None:
        for name, digest in SAMPLE_HASHES.items():
            result = check_file(ROOT / "examples" / name)
            self.assertEqual(result.ast_hash, digest, name)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(OSError):
            check_file(ROOT / "examples" / "agents" / "missing.prout")


if __name__ == "__main__":
    unittest.main()
