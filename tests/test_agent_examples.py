"""Adoption examples: a clean contract and an E-POLICY failure for each gate."""
from __future__ import annotations

import unittest
from pathlib import Path

import support  # noqa: F401
from prout_api import check_file

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "examples" / "agents"


class AgentExampleTests(unittest.TestCase):
    def test_good_contracts_have_no_errors(self) -> None:
        cases = (
            ("support_reply.prout", None, "draft_reply"),
            ("invoice.prout", None, "issue_invoice"),
            ("subscription_cancel.prout", None, "cancel_subscription"),
            ("github_triage.prout", ["github"], "triage_open_issues"),
        )
        for name, allow_net, task in cases:
            result = check_file(AGENTS / name, allow_net=allow_net)
            self.assertTrue(result.ok, result.diagnostics)
            self.assertEqual(result.diagnostics, ())
            self.assertEqual([plan.name for plan in result.plans], [task])

    def test_broken_contracts_fail_with_e_policy_only(self) -> None:
        cases = (
            ("support_reply_broken.prout", None, "emit to an external party has no approval gate"),
            ("invoice_broken.prout", None, "spend has no approval gate"),
            ("subscription_cancel_broken.prout", None, "a contract change has no approval gate"),
        )
        for name, allow_net, message in cases:
            result = check_file(AGENTS / name, allow_net=allow_net)
            self.assertFalse(result.ok)
            self.assertEqual([item.code for item in result.diagnostics], ["E-POLICY"])
            self.assertIn(message, result.diagnostics[0].message)

    def test_github_triage_needs_the_allowlist_and_a_positive_cap(self) -> None:
        good = check_file(AGENTS / "github_triage.prout")
        self.assertFalse(good.ok)
        self.assertEqual(
            [item.message for item in good.diagnostics],
            [
                "network target 'github' is not allowed by policy",
                "network target 'github' is not allowed by policy",
            ],
        )
        broken = check_file(AGENTS / "github_triage_broken.prout", allow_net=["github"])
        self.assertFalse(broken.ok)
        self.assertEqual([item.code for item in broken.diagnostics], ["E-POLICY", "E-POLICY"])
        text = "\n".join(item.message for item in broken.diagnostics)
        self.assertIn("network target 'gist' is not allowed by policy", text)
        self.assertIn("net effect needs a positive hourly rate limit", text)


if __name__ == "__main__":
    unittest.main()
