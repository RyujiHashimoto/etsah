"""Line-oriented program parser: type, op, flow, and indentation."""
from __future__ import annotations

import unittest

import support  # noqa: F401
from etsah import Parser, ProutError
from support import parse, snippet


class TestDeclarations(unittest.TestCase):
    def test_opaque_and_record_types(self) -> None:
        program = parse(
            """
            type Order {
              id: Id
              total: Money
              status: Status
            }

            type Money
            type Id
            """
        )
        by_name = {t.name: t for t in program.types}
        self.assertFalse(by_name["Order"].opaque)
        self.assertEqual(
            by_name["Order"].fields,
            {"id": "Id", "total": "Money", "status": "Status"},
        )
        self.assertTrue(by_name["Money"].opaque)
        self.assertEqual(by_name["Money"].fields, {})
        self.assertEqual(by_name["Order"].line, 1)

    def test_generic_field_type_is_preserved(self) -> None:
        program = parse(
            """
            op f(a: Int, b: List<String>) -> List<Int>
              impl native
            """
        )
        op = program.ops[0]
        self.assertEqual(op.params, [("a", "Int"), ("b", "List<String>")])
        self.assertEqual(op.ret, "List<Int>")

    def test_op_properties(self) -> None:
        program = parse(
            r"""
            op orders.get(order_id: Id) -> Order
              doc "注文 #1"
              eff read(orders), write(payments)
              pre amount <= order.total
              post result.status == "refunded"
              impl native
            """
        )
        op = program.ops[0]
        self.assertEqual(op.name, "orders.get")
        self.assertEqual(op.params, [("order_id", "Id")])
        self.assertEqual(op.ret, "Order")
        self.assertEqual(op.doc, "注文 #1")
        self.assertEqual(op.effects, [("read", "orders"), ("write", "payments")])
        self.assertEqual(len(op.pres), 1)
        self.assertEqual(op.pres[0].name, "<=")
        self.assertEqual(op.posts[0].name, "==")
        self.assertTrue(op.native)
        self.assertIsNone(op.impl)

    def test_impl_call_is_not_native(self) -> None:
        program = parse(
            """
            op refund(order: Order, amount: Money) -> Receipt
              impl stripe.refund(order, amount)
            """
        )
        op = program.ops[0]
        self.assertFalse(op.native)
        self.assertIsNotNone(op.impl)
        assert op.impl is not None
        self.assertEqual(op.impl.kind, "call")
        self.assertEqual(op.impl.name, "stripe.refund")

    def test_flow_statements_and_line_numbers(self) -> None:
        text = snippet(
            """
            # comment that must not shift the following line numbers

            flow handle_refund(order_id: Id) -> Receipt
              doc "返金"
              summary "注文を返す"
              eff read(orders)
              let order = orders.get(order_id)
              return order
            """
        )
        flow = Parser(text).parse().flows[0]
        self.assertEqual(flow.name, "handle_refund")
        self.assertEqual(flow.params, [("order_id", "Id")])
        self.assertEqual(flow.doc, "返金")
        self.assertEqual(flow.summary, "注文を返す")
        self.assertEqual(flow.effects, [("read", "orders")])
        self.assertEqual([s.kind for s in flow.body], ["let", "return"])
        self.assertEqual(flow.body[0].name, "order")
        self.assertEqual(flow.body[0].expr.name, "orders.get")  # type: ignore[union-attr]
        # Blank line + comment occupy lines 1-2; the header is line 3.
        self.assertEqual(flow.line, 3)
        self.assertEqual(flow.body[0].line, 7)
        self.assertEqual(flow.body[1].line, 8)

    def test_hash_inside_a_string_is_not_a_comment(self) -> None:
        program = parse(
            """
            op f() -> Int
              doc "keep # this"
              impl native
            """
        )
        self.assertEqual(program.ops[0].doc, "keep # this")

    def test_tail_comment_is_stripped(self) -> None:
        program = parse(
            """
            type Money # opaque cents
            """
        )
        self.assertEqual(program.types[0].name, "Money")
        self.assertTrue(program.types[0].opaque)

    def test_dotted_flow_name_is_rejected(self) -> None:
        with self.assertRaises(ProutError) as ctx:
            parse(
                """
                flow orders.get(id: Id) -> Order
                  return id
                """
            )
        self.assertEqual(ctx.exception.line, 1)

    def test_unknown_declaration_property_and_statement(self) -> None:
        with self.assertRaises(ProutError) as ctx:
            parse(
                """
                widget Foo
                """
            )
        self.assertEqual(ctx.exception.line, 1)

        with self.assertRaises(ProutError) as ctx:
            parse(
                """
                op f() -> Int
                  color red
                """
            )
        self.assertEqual(ctx.exception.line, 2)

        with self.assertRaises(ProutError) as ctx:
            parse(
                """
                flow f() -> Int
                  loop forever
                """
            )
        self.assertEqual(ctx.exception.line, 2)

    def test_bad_parameter_effect_and_let(self) -> None:
        with self.assertRaises(ProutError) as ctx:
            parse(
                """
                op f(amount) -> Int
                  impl native
                """
            )
        self.assertEqual(ctx.exception.line, 1)

        with self.assertRaises(ProutError) as ctx:
            parse(
                """
                op f() -> Int
                  eff read orders
                """
            )
        self.assertEqual(ctx.exception.line, 2)

        with self.assertRaises(ProutError) as ctx:
            parse(
                """
                flow f() -> Int
                  let = 1
                """
            )
        self.assertEqual(ctx.exception.line, 2)

    def test_unterminated_type_and_bad_field(self) -> None:
        with self.assertRaises(ProutError) as ctx:
            parse(
                """
                type Order {
                  id: Id
                """
            )
        self.assertEqual(ctx.exception.line, 1)

        with self.assertRaises(ProutError) as ctx:
            parse(
                """
                type Order {
                  1bad: Int
                }
                """
            )
        self.assertEqual(ctx.exception.line, 2)

    def test_empty_source_is_an_empty_program(self) -> None:
        program = Parser("\n# only a comment\n\n").parse()
        self.assertEqual(program.types, [])
        self.assertEqual(program.ops, [])
        self.assertEqual(program.flows, [])


class TestIndentation(unittest.TestCase):
    def test_odd_indent_is_rejected(self) -> None:
        with self.assertRaises(ProutError) as ctx:
            Parser("type T {\n a: Int\n}\n").parse()
        self.assertEqual(ctx.exception.line, 2)

    def test_tabs_are_rejected(self) -> None:
        with self.assertRaises(ProutError) as ctx:
            Parser("type T {\n\ta: Int\n}\n").parse()
        self.assertEqual(ctx.exception.line, 2)

    def test_type_fields_must_be_exactly_two_spaces(self) -> None:
        with self.assertRaises(ProutError) as ctx:
            Parser("type T {\n    a: Int\n}\n").parse()
        self.assertEqual(ctx.exception.line, 2)

    def test_top_level_must_start_at_column_zero(self) -> None:
        with self.assertRaises(ProutError) as ctx:
            Parser("  type T\n").parse()
        self.assertEqual(ctx.exception.line, 1)

    def test_deeper_op_property_is_not_a_property(self) -> None:
        with self.assertRaises(ProutError) as ctx:
            Parser("op f() -> Int\n    impl native\n").parse()
        self.assertEqual(ctx.exception.line, 2)

    def test_two_space_body_is_accepted(self) -> None:
        program = Parser("op f() -> Int\n  impl native\n").parse()
        self.assertTrue(program.ops[0].native)


if __name__ == "__main__":
    unittest.main()
