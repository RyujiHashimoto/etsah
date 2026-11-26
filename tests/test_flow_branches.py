"""Branching flows and the program-counter label.

An ``if`` condition guards the values produced inside its branches. A public
value returned under a ``pii`` condition is therefore a ``pii`` return, and
the checker reports ``E-IFLOW-RET`` unless the condition is declassified.
"""
from __future__ import annotations

import textwrap
import unittest

from support import check, codes, parse
from etsah import ProutError, ast_hash, program_to_dict

PRELUDE = textwrap.dedent(
    """
    label pii

    type Id
    type Customer {
      id: Id
      email: String @pii
    }

    op customers.get(id: Id) -> Customer
      eff read(customers)
      impl native

    op send_receipt(to: String @pii) -> Bool
      eff net(email)
      sends to -> net(email)
      impl native

    op ping() -> Bool
      eff net(email)
      sends none -> net(email)
      impl native
    """
)


def run(body: str, declassify: str | None = None, **kwargs):
    policy = "policy\n  sink net(email) accepts pii\n"
    if declassify:
        policy += f"  declassify pii in {declassify} require role support\n"
    return check(PRELUDE + policy + textwrap.dedent(body), allow_net=["email"], **kwargs)


class TestBranchParsing(unittest.TestCase):
    def test_if_requires_a_body(self) -> None:
        with self.assertRaises(ProutError) as caught:
            parse(
                """
                flow f() -> Int
                  if true
                  return 1
                """
            )
        self.assertIn("if requires a body", str(caught.exception))

    def test_else_requires_a_body(self) -> None:
        with self.assertRaises(ProutError) as caught:
            parse(
                """
                flow f() -> Int
                  if true
                    return 1
                  else
                """
            )
        self.assertIn("else requires a body", str(caught.exception))

    def test_task_body_rejects_if(self) -> None:
        with self.assertRaises(ProutError) as caught:
            parse(
                """
                task t() -> Int
                  if true
                    return 1
                """
            )
        self.assertIn("only available in flow", str(caught.exception))


class TestBranchTypes(unittest.TestCase):
    def test_condition_must_be_bool(self) -> None:
        diags = run(
            """
            flow f(x: Int) -> Int
              if x
                return 1
              else
                return 2
            """
        )
        self.assertIn("E-TYPE", codes(diags, "error"))

    def test_both_branches_must_return(self) -> None:
        diags = run(
            """
            flow f(x: Bool) -> Int
              if x
                return 1
            """
        )
        self.assertIn("E-FLOW", codes(diags, "error"))

    def test_else_if_chain_returns(self) -> None:
        diags = run(
            """
            flow f(x: Int) -> String
              if x == 1
                return "one"
              else
                if x == 2
                  return "two"
                else
                  return "many"
            """
        )
        self.assertEqual(codes(diags, "error"), [])

    def test_statement_after_both_branches_return_is_unreachable(self) -> None:
        diags = run(
            """
            flow f(x: Bool) -> Int
              if x
                return 1
              else
                return 2
              return 3
            """
        )
        self.assertIn("E-FLOW", codes(diags, "error"))

    def test_branch_variable_is_scoped_to_the_branch(self) -> None:
        diags = run(
            """
            flow f(x: Bool) -> String
              if x
                let label = "yes"
                return label
              else
                let label = "no"
              return label
            """
        )
        self.assertIn("E-UNDEF", codes(diags, "error"))


class TestImplicitFlow(unittest.TestCase):
    def test_pii_condition_taints_public_return(self) -> None:
        diags = run(
            """
            flow classify(id: Id) -> String
              eff read(customers)
              let c = customers.get(id)
              if c.email == "blocked@example.com"
                return "blocked"
              else
                return "allowed"
            """
        )
        self.assertEqual(codes(diags, "error"), ["E-IFLOW-RET", "E-IFLOW-RET"])

    def test_public_condition_allows_public_return(self) -> None:
        diags = run(
            """
            flow classify(id: Id, blocked: Bool) -> String
              eff read(customers)
              let c = customers.get(id)
              if blocked
                return "blocked"
              else
                return "allowed"
            """
        )
        self.assertEqual(diags, [])

    def test_declassified_condition_removes_the_pc_label(self) -> None:
        diags = run(
            """
            flow masked(id: Id) -> String
              eff read(customers)
              let c = customers.get(id)
              let blocked = declassify(c.email == "blocked@example.com", pii, "判定結果だけを公開する")
              if blocked
                return "blocked"
              else
                return "allowed"
            """,
            declassify="masked",
        )
        self.assertEqual(codes(diags, "error"), [])
        self.assertIn("W-DECLASS", codes(diags, "warning"))

    def test_tainted_let_is_caught_on_return(self) -> None:
        diags = run(
            """
            flow classify(id: Id) -> String
              eff read(customers)
              let c = customers.get(id)
              if c.email == "x"
                let answer = "yes"
                return answer
              else
                let answer = "no"
                return answer
            """
        )
        self.assertEqual(codes(diags, "error"), ["E-IFLOW-RET", "E-IFLOW-RET"])

    def test_nested_conditions_accumulate(self) -> None:
        diags = run(
            """
            flow classify(id: Id) -> String
              eff read(customers)
              let c = customers.get(id)
              if c.email == "x"
                if true
                  return "yes"
                else
                  return "no"
              else
                return "other"
            """
        )
        self.assertGreaterEqual(codes(diags, "error").count("E-IFLOW-RET"), 2)

    def test_pii_return_type_accepts_tainted_value(self) -> None:
        diags = run(
            """
            flow pick(id: Id) -> String @pii
              eff read(customers)
              let c = customers.get(id)
              if true
                return c.email
              else
                return c.email
            """
        )
        self.assertEqual(codes(diags, "error"), [])


class TestBranchEffects(unittest.TestCase):
    def test_branch_effects_are_unioned(self) -> None:
        diags = run(
            """
            flow receipt(id: Id, vip: Bool) -> Bool
              eff read(customers)
              let c = customers.get(id)
              if vip
                return send_receipt(c.email)
              else
                return false
            """
        )
        self.assertIn("E-EFF", codes(diags, "error"))

    def test_declared_branch_effects_pass(self) -> None:
        diags = run(
            """
            flow receipt(id: Id, vip: Bool) -> Bool
              eff read(customers), net(email)
              let c = customers.get(id)
              if vip
                return send_receipt(c.email)
              else
                return false
            """
        )
        self.assertEqual(codes(diags, "error"), [])

    def test_condition_call_effects_are_counted(self) -> None:
        diags = run(
            """
            flow f() -> Int
              if ping()
                return 1
              else
                return 2
            """
        )
        self.assertIn("E-EFF", codes(diags, "error"))


class TestBranchAst(unittest.TestCase):
    SOURCE = """
        flow f(x: Bool) -> Int
          if x
            return 1
          else
            return 2
    """

    def test_branch_is_serialized(self) -> None:
        body = program_to_dict(parse(self.SOURCE))["flows"][0]["body"]
        self.assertEqual(body[0]["kind"], "if")
        self.assertEqual(len(body[0]["body"]), 1)
        self.assertEqual(len(body[0]["orelse"]), 1)

    def test_flat_statements_keep_their_old_shape(self) -> None:
        body = program_to_dict(
            parse(
                """
                flow f() -> Int
                  return 1
                """
            )
        )["flows"][0]["body"]
        self.assertEqual(
            body,
            [{"kind": "return", "name": "", "expr": {"kind": "lit", "type": "int", "value": 1}}],
        )

    def test_branch_hash_is_stable(self) -> None:
        first = parse(self.SOURCE)
        second = parse(self.SOURCE)
        self.assertEqual(ast_hash(first), ast_hash(second))
        self.assertEqual(len(ast_hash(first)), 12)

    def test_branch_hash_differs_from_flat_flow(self) -> None:
        flat = parse(
            """
            flow f(x: Bool) -> Int
              return 1
            """
        )
        self.assertNotEqual(ast_hash(parse(self.SOURCE)), ast_hash(flat))


if __name__ == "__main__":
    unittest.main()
