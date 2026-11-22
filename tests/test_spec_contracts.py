"""Regression tests for SPEC claims that were previously known gaps.

These tests used to be ``expectedFailure`` while the implementation caught up.
They now run as normal tests, so weakening them would hide a regression.
"""
from __future__ import annotations

import subprocess
import sys
import unittest

import support  # noqa: F401
from prout import ast_hash
from proutc import DEFAULT_REGISTRY, find_patterns, load_registry
from support import PATTERNS, check, codes, parse


class TestSpecContracts(unittest.TestCase):
    def test_pre_and_post_order_do_not_change_the_hash(self) -> None:
        """SPEC.md: 正規形が一意 — 条件の並び順はハッシュに影響しない。

        Preconditions and postconditions are conjunctions, so source order is
        not meaningful. ``program_to_dict`` sorts both lists by their canonical
        expression JSON before hashing.
        """
        pre_first = parse(
            """
            op f(x: Int) -> Int
              pre x > 0
              pre x < 10
              impl native
            """
        )
        pre_second = parse(
            """
            op f(x: Int) -> Int
              pre x < 10
              pre x > 0
              impl native
            """
        )
        self.assertEqual(ast_hash(pre_first), ast_hash(pre_second))

        post_first = parse(
            """
            op g(x: Int) -> Int
              post result >= 0
              post result <= 10
              impl native
            """
        )
        post_second = parse(
            """
            op g(x: Int) -> Int
              post result <= 10
              post result >= 0
              impl native
            """
        )
        self.assertEqual(ast_hash(post_first), ast_hash(post_second))

    def test_declared_effects_must_match_an_effect_free_impl(self) -> None:
        """SPEC.md: 宣言と実際の効果が一致しなければエラー。

        An expression impl that performs no effects must still report an extra
        declared effect. ``impl native`` remains trusted because its body is
        not available for inference.
        """
        diags = check(
            """
            op f() -> Int
              eff read(orders)
              impl 1
            """
        )
        self.assertIn("E-EFF", codes(diags, "error"))
        eff = next(d for d in diags if d.code == "E-EFF")
        self.assertIn("unused read(orders)", eff.message)

    def test_find_scores_slot_descriptions(self) -> None:
        """patterns/registry/README.md: find は intent、tags、name、スロット説明の一致を点数化する。

        ``Supabase`` occurs only in the description of auth's ``provider`` slot.
        ``find_patterns`` scores slot descriptions as well as slot names.
        """
        found = [item.name for _score, item in find_patterns("Supabase", load_registry(DEFAULT_REGISTRY))]
        self.assertIn("auth", found)

    def test_find_command_shows_effects(self) -> None:
        """patterns/registry/README.md: 上位3件と、必須スロット・任意スロット・効果を表示する。

        ``proutc find`` prints at most three matches, with required slots,
        optional slots, effects, and tags.
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
        self.assertIn("showing", proc.stdout)
        self.assertLessEqual(proc.stdout.count("score="), 3)


if __name__ == "__main__":
    unittest.main()
