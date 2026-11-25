"""One fictional program that declares both kinds of policy."""
from __future__ import annotations

import unittest
from pathlib import Path

import support  # noqa: F401
from etsah import Parser
from support import check, codes

ROOT = Path(__file__).resolve().parents[1]


class CombinedPolicyExampleTests(unittest.TestCase):
    def test_safe_file_passes_and_broken_file_is_e_policy(self) -> None:
        safe_text = (ROOT / "examples" / "combined_policy_safe.etsah").read_text(encoding="utf-8")
        broken_text = (ROOT / "examples" / "combined_policy_broken.etsah").read_text(encoding="utf-8")
        for text in (safe_text, broken_text):
            kinds = [item.kind for item in Parser(text).parse().policies]
            self.assertEqual(kinds, ["flow", "task"])
        safe = check(safe_text, allow_net=["email"])
        self.assertEqual(codes(safe, "error"), [])
        broken = check(broken_text, allow_net=["email"])
        self.assertEqual(codes(broken, "error"), ["E-POLICY"])
        self.assertTrue(any("has no approval gate" in item.message for item in broken if item.severity == "error"))
