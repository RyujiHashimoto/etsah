#!/usr/bin/env python3
"""Regression tests for the Etsah contract checker (etsah.py).

Run from the repository root:

    python3 -m unittest discover -s tests -v

Each test names the behaviour it pins down. The three groups cover the
checker fixes for comparison typing, unary `!`, and flow returns.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etsah import Checker, Parser, ProutError, parse_expr  # noqa: E402

HEADER = """
type Order {
  id: Id
  total: Money
  status: Status
}
type Money
type Id
type Status
type Receipt {
  status: Status
}
"""


def check(src: str):
    program = Parser(HEADER + src).parse()
    return Checker(program).check()


def codes(diags):
    return [d.code for d in diags if d.severity == "error"]


class ComparisonTyping(unittest.TestCase):
    def test_money_compared_to_string_is_a_type_error(self):
        diags = check(
            """
op refund(order: Order, amount: Money) -> Receipt
  doc "refund"
  eff read(orders)
  pre amount == "paid"
  impl native
"""
        )
        self.assertIn("E-TYPE", codes(diags))

    def test_status_compared_to_string_literal_is_allowed(self):
        diags = check(
            """
op refund(order: Order, amount: Money) -> Receipt
  doc "refund"
  eff read(orders)
  pre order.status == "paid"
  impl native
"""
        )
        self.assertNotIn("E-TYPE", codes(diags))

    def test_money_ordering_against_money_is_allowed(self):
        diags = check(
            """
op refund(order: Order, amount: Money) -> Receipt
  doc "refund"
  eff read(orders)
  pre amount <= order.total
  impl native
"""
        )
        self.assertEqual([], codes(diags))

    def test_ordering_between_money_and_string_is_a_type_error(self):
        diags = check(
            """
op refund(order: Order, amount: Money) -> Receipt
  doc "refund"
  eff read(orders)
  pre amount < "paid"
  impl native
"""
        )
        self.assertIn("E-TYPE", codes(diags))


class UnaryNot(unittest.TestCase):
    SRC = """
op check(order: Order, flag: Bool) -> Receipt
  doc "check"
  eff read(orders)
  pre !flag
  impl native
flow f(order: Order) -> Receipt
  doc "f"
  eff read(orders)
  let r = check(order, {arg})
  return r
"""

    def test_not_true_is_proven_false_with_readable_message(self):
        diags = check(self.SRC.format(arg="true"))
        pre = [d for d in diags if d.code == "E-PRE"]
        self.assertEqual(1, len(pre))
        self.assertIn("(!true)", pre[0].message)

    def test_not_false_passes_without_a_guard_warning(self):
        diags = check(self.SRC.format(arg="false"))
        self.assertEqual([], codes(diags))
        self.assertEqual([], [d for d in diags if d.code == "W-PRE"])

    def test_not_on_non_bool_is_a_type_error(self):
        diags = check(
            """
op check(order: Order, flag: Bool) -> Receipt
  doc "check"
  eff read(orders)
  pre !order.total
  impl native
"""
        )
        self.assertIn("E-TYPE", codes(diags))


class FlowReturn(unittest.TestCase):
    def test_flow_without_return_is_an_error(self):
        diags = check(
            """
op get(order: Order) -> Order
  doc "get"
  eff read(orders)
  impl native
flow f(order: Order) -> Order
  doc "f"
  eff read(orders)
  let x = get(order)
"""
        )
        self.assertIn("E-FLOW", codes(diags))

    def test_statement_after_return_is_an_error(self):
        diags = check(
            """
op get(order: Order) -> Order
  doc "get"
  eff read(orders)
  impl native
flow f(order: Order) -> Order
  doc "f"
  eff read(orders)
  let x = get(order)
  return x
  let y = get(order)
"""
        )
        self.assertIn("E-FLOW", codes(diags))

    def test_flow_ending_with_return_is_clean(self):
        diags = check(
            """
op get(order: Order) -> Order
  doc "get"
  eff read(orders)
  impl native
flow f(order: Order) -> Order
  doc "f"
  eff read(orders)
  let x = get(order)
  return x
"""
        )
        self.assertEqual([], codes(diags))


class StringLiteralDiagnostics(unittest.TestCase):
    """Malformed string escapes must surface as ProutError diagnostics,
    never as a raw json.JSONDecodeError traceback."""

    def test_bad_escape_in_doc_is_a_prout_error(self):
        with self.assertRaises(ProutError):
            Parser(
                HEADER
                + """
op refund(order: Order, amount: Money) -> Receipt
  doc "bad \\x escape"
  eff read(orders)
  impl native
"""
            ).parse()

    def test_bad_escape_in_expression_is_a_prout_error(self):
        with self.assertRaises(ProutError):
            parse_expr('amount == "bad \\x"', 1)

    def test_bad_escape_in_parse_string_is_a_prout_error(self):
        with self.assertRaises(ProutError):
            Parser.parse_string('"bad \\x"', 1)

    def test_valid_escapes_still_decode(self):
        self.assertEqual(Parser.parse_string('"a\\nb"', 1), "a\nb")
        expr = parse_expr('amount == "paid"', 1)
        self.assertEqual(expr.right.value, "paid")


if __name__ == "__main__":
    unittest.main()
