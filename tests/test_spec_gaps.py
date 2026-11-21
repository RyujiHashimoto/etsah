"""Behavior the specs describe and the current code does not implement.

These tests are ``expectedFailure`` on purpose. They should start passing when
the implementation catches up; until then they document the gap without
failing CI. Do not "fix" them by weakening the assertion.
"""
from __future__ import annotations

import subprocess
import sys
import unittest

import support  # noqa: F401
from prout import ast_hash
from proutc import DEFAULT_REGISTRY, find_patterns, load_registry
from support import PATTERNS, check, codes, parse


class TestSpecGaps(unittest.TestCase):
    @unittest.expectedFailure
    def test_precondition_order_does_not_change_the_hash(self) -> None:
        """SPEC.md: 正規形が一意 — 同じ意味のプログラムは同じAST・同じハッシュになる。

        Preconditions are a conjunction, so source order is not meaningful.
        ``program_to_dict`` keeps ``pre`` and ``post`` in source order, and
        these two programs hash differently. Effects, fields, and declarations
        are sorted; the contract lists are not.
        """
        first = parse(
            """
            op f(x: Int) -> Int
              pre x > 0
              pre x < 10
              impl native
            """
        )
        second = parse(
            """
            op f(x: Int) -> Int
              pre x < 10
              pre x > 0
              impl native
            """
        )
        self.assertEqual(ast_hash(first), ast_hash(second))

    @unittest.expectedFailure
    def test_declared_effects_must_match_an_effect_free_impl(self) -> None:
        """SPEC.md: 宣言と実際の効果が一致しなければエラー。

        ``check_op`` only compares effects when the impl infers at least one
        (``if inferred and declared != inferred``). An impl that performs
        nothing therefore does not report the extra declared effect. Flows do
        compare both directions, including the empty set.
        """
        diags = check(
            """
            op f() -> Int
              eff read(orders)
              impl 1
            """
        )
        self.assertIn("E-EFF", codes(diags, "error"))

    @unittest.expectedFailure
    def test_find_scores_slot_descriptions(self) -> None:
        """patterns/registry/README.md: find は intent、tags、name、スロット説明の一致を点数化する。

        ``Supabase`` occurs only in the description of auth's ``provider`` slot.
        ``find_patterns`` matches slot names, not descriptions, so auth scores 0.
        """
        found = [item.name for _score, item in find_patterns("Supabase", load_registry(DEFAULT_REGISTRY))]
        self.assertIn("auth", found)

    @unittest.expectedFailure
    def test_find_command_shows_effects(self) -> None:
        """patterns/registry/README.md: 上位3件と、必須スロット・任意スロット・効果を表示する。

        ``proutc find`` prints required slots, optional slots, and tags. It does
        not print the manifest's ``effects``.
        """
        proc = subprocess.run(
            [sys.executable, str(PATTERNS / "proutc.py"), "find", "auth"],
            cwd=PATTERNS,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("read(users)", proc.stdout)
        self.assertIn("write(sessions)", proc.stdout)


if __name__ == "__main__":
    unittest.main()
