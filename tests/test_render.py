"""Japanese titles for the codes a non-programmer meets in the examples."""
from __future__ import annotations

import unittest

import support  # noqa: F401
from render import ISSUE_HUMAN


class HumanReasonTests(unittest.TestCase):
    def test_policy_and_argument_flow_have_one_japanese_line(self) -> None:
        for code in ("E-POLICY", "E-IFLOW-ARG"):
            title, reason = ISSUE_HUMAN[code]
            self.assertTrue(title)
            self.assertNotIn("\n", title)
            self.assertNotIn("\n", reason)
            self.assertTrue(any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in reason))
            self.assertNotEqual(title, code)
            self.assertNotIn("E-POLICY", reason)
            self.assertNotIn("E-IFLOW", reason)
