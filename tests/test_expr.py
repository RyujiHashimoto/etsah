"""Pratt expression parser: literals, calls, fields, and precedence."""
from __future__ import annotations

import unittest

import support  # noqa: F401
from prout import ProutError, evaluate, linear, parse_expr


def _shape(source: str):
    def walk(expr):
        if expr is None:
            return None
        if expr.kind == "bin":
            return (expr.name, walk(expr.left), walk(expr.right))
        if expr.kind == "lit":
            return (expr.name, expr.value)
        if expr.kind == "var":
            return ("var", expr.name)
        if expr.kind == "field":
            return ("field", expr.name, walk(expr.base))
        if expr.kind == "call":
            return ("call", expr.name, [walk(arg) for arg in expr.args])
        raise AssertionError(expr.kind)

    return walk(parse_expr(source, 1))


class TestExprShapes(unittest.TestCase):
    def test_literals(self) -> None:
        self.assertEqual(_shape("42"), ("int", 42))
        self.assertEqual(_shape("true"), ("bool", True))
        self.assertEqual(_shape("false"), ("bool", False))
        self.assertEqual(_shape(r'"say \"hi\""'), ("str", 'say "hi"'))
        self.assertEqual(_shape('"注文"'), ("str", "注文"))

    def test_variables_fields_and_calls(self) -> None:
        self.assertEqual(_shape("amount"), ("var", "amount"))
        self.assertEqual(
            _shape("order.total"),
            ("field", "total", ("var", "order")),
        )
        self.assertEqual(
            _shape("order.customer.email"),
            ("field", "email", ("field", "customer", ("var", "order"))),
        )
        # Dots inside an identifier are part of the callee, matching `orders.get`.
        self.assertEqual(
            _shape("orders.get(order_id)"),
            ("call", "orders.get", [("var", "order_id")]),
        )
        self.assertEqual(
            _shape("refund(order, order.total)"),
            (
                "call",
                "refund",
                [("var", "order"), ("field", "total", ("var", "order"))],
            ),
        )
        self.assertEqual(
            _shape("f(1 + 2, 3 * 4)"),
            ("call", "f", [("+", ("int", 1), ("int", 2)), ("*", ("int", 3), ("int", 4))]),
        )

    def test_true_with_call_syntax_is_a_call(self) -> None:
        self.assertEqual(_shape("true()"), ("call", "true", []))

    def test_parentheses_and_trailing_junk(self) -> None:
        # Equality of two non-literals is not constant-folded; arithmetic is.
        self.assertEqual(linear(parse_expr("(1 + 2) * 3", 1)), ({}, 9))
        self.assertEqual(evaluate(parse_expr("(1 + 2) * 3 <= 9", 1)), True)
        with self.assertRaises(ProutError):
            parse_expr("1 2", 1)
        with self.assertRaises(ProutError):
            parse_expr("", 1)


class TestPrecedence(unittest.TestCase):
    def test_multiplicative_binds_tighter_than_additive(self) -> None:
        self.assertEqual(_shape("1 + 2 * 3"), ("+", ("int", 1), ("*", ("int", 2), ("int", 3))))
        self.assertEqual(_shape("1 * 2 + 3"), ("+", ("*", ("int", 1), ("int", 2)), ("int", 3)))
        self.assertEqual(linear(parse_expr("1 + 2 * 3", 1)), ({}, 7))
        self.assertEqual(evaluate(parse_expr("1 + 2 * 3 <= 7", 1)), True)
        self.assertEqual(evaluate(parse_expr("1 + 2 * 3 < 7", 1)), False)

    def test_addition_and_multiplication_are_left_associative(self) -> None:
        self.assertEqual(
            _shape("1 - 2 - 3"),
            ("-", ("-", ("int", 1), ("int", 2)), ("int", 3)),
        )
        self.assertEqual(linear(parse_expr("1 - 2 - 3", 1)), ({}, -4))
        self.assertEqual(linear(parse_expr("2 * 3 * 4", 1)), ({}, 24))
        # Right-associative subtraction would be 1 - (2 - 3) = 2.
        self.assertNotEqual(linear(parse_expr("1 - 2 - 3", 1)), ({}, 2))

    def test_comparison_binds_looser_than_arithmetic_and_does_not_chain(self) -> None:
        self.assertEqual(
            _shape("1 + 2 == 3 + 4"),
            ("==", ("+", ("int", 1), ("int", 2)), ("+", ("int", 3), ("int", 4))),
        )
        self.assertEqual(linear(parse_expr("1 + 2", 1)), ({}, 3))
        self.assertEqual(evaluate(parse_expr("1 + 2 <= 3", 1)), True)
        self.assertEqual(evaluate(parse_expr("1 + 2 < 3", 1)), False)
        with self.assertRaises(ProutError):
            parse_expr("a < b < c", 1)

    def test_and_binds_tighter_than_or(self) -> None:
        self.assertEqual(
            _shape("a && b || c && d"),
            ("||", ("&&", ("var", "a"), ("var", "b")), ("&&", ("var", "c"), ("var", "d"))),
        )
        self.assertEqual(evaluate(parse_expr("1 < 2 && 3 < 1", 1)), False)
        self.assertEqual(evaluate(parse_expr("1 < 2 || 3 < 1", 1)), True)
        self.assertEqual(evaluate(parse_expr("1 < 2 && 3 < 4 || 0 > 1", 1)), True)

    def test_unary_minus_is_tighter_than_addition(self) -> None:
        # Value-level check so the test does not depend on the unary encoding.
        self.assertEqual(linear(parse_expr("-3", 1)), ({}, -3))
        self.assertEqual(linear(parse_expr("-1 + 2", 1)), ({}, 1))
        self.assertEqual(evaluate(parse_expr("-1 + 2 <= 1", 1)), True)
        self.assertEqual(evaluate(parse_expr("-1 + 2 > 1", 1)), False)
        self.assertEqual(linear(parse_expr("-(1 + 2)", 1)), ({}, -3))

    def test_unary_not_is_tighter_than_and(self) -> None:
        self.assertEqual(evaluate(parse_expr("!false", 1)), True)
        self.assertEqual(evaluate(parse_expr("!true", 1)), False)
        self.assertEqual(evaluate(parse_expr("!false && false", 1)), False)
        self.assertEqual(evaluate(parse_expr("!false || false", 1)), True)

    def test_not_equal_and_relational_operators(self) -> None:
        # `!=` is accepted by the implementation; SPEC.md's operator list omits it.
        self.assertEqual(evaluate(parse_expr("1 != 2", 1)), True)
        self.assertEqual(evaluate(parse_expr("1 != 1", 1)), False)
        self.assertEqual(evaluate(parse_expr("1 < 1", 1)), False)
        self.assertEqual(evaluate(parse_expr("1 <= 1", 1)), True)
        self.assertEqual(evaluate(parse_expr("2 > 1", 1)), True)
        self.assertEqual(evaluate(parse_expr("2 >= 3", 1)), False)


if __name__ == "__main__":
    unittest.main()
