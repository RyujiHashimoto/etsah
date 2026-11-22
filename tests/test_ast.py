"""Canonical AST and hash stability.

SPEC: structurally equivalent programs have one AST and one hash. Whitespace,
comments, declaration order, and pre/post conjunct order are not meaning.
Logical equivalence beyond that structural normalization is not attempted.
"""
from __future__ import annotations

import hashlib
import json
import unittest

import support
from prout import Parser, ast_hash, program_to_dict
from support import parse, snippet

BASE = """
    type Receipt {
      id: Id
      amount: Money
    }

    type Id
    type Money

    type Order {
      total: Money
      id: Id
    }

    op stripe.refund(order: Order, amount: Money) -> Receipt
      doc "charge"
      eff net(stripe), write(payments)
      pre amount <= order.total
      impl native

    op orders.get(id: Id) -> Order
      doc "load"
      eff read(orders)
      impl native

    flow beta(id: Id) -> Id
      doc "echo"
      return id

    flow alpha(id: Id) -> Receipt
      doc "run"
      summary "do it"
      eff read(orders), net(stripe), write(payments)
      let order = orders.get(id)
      let receipt = stripe.refund(order, order.total)
      return receipt
"""

REORDERED = """
    type Money
    type Order {
      id: Id
      total: Money
    }
    type Id
    type Receipt {
      amount: Money
      id: Id
    }

    op orders.get(id: Id) -> Order
      doc "load"
      eff read(orders)
      impl native

    op stripe.refund(order: Order, amount: Money) -> Receipt
      doc "charge"
      eff write(payments), net(stripe)
      pre amount<=order.total
      impl native

    flow alpha(id: Id) -> Receipt
      summary "do it"
      doc "run"
      eff write(payments), read(orders), net(stripe)
      let order = orders.get( id )
      let receipt = stripe.refund( order, order.total )
      return receipt

    flow beta(id: Id) -> Id
      doc "echo"
      return id
"""

COMMENTED = """
    # leading note

    type Receipt {
      id: Id
      amount: Money
    }
    type Id
    type Money  # cents

    type Order {
      total: Money
      id: Id
    }

    op stripe.refund(order: Order, amount: Money) -> Receipt
      doc "charge"  # keep
      eff net(stripe), write(payments)
      pre amount <= order.total
      impl native

    op orders.get(id: Id) -> Order
      doc "load"
      eff read(orders)
      impl native

    flow beta(id: Id) -> Id
      doc "echo"
      return id

    flow alpha(id: Id) -> Receipt
      doc "run"
      summary "do it"
      eff read(orders), net(stripe), write(payments)
      let order = orders.get(id)
      let receipt = stripe.refund(order, order.total)
      return receipt
"""


class TestAstHash(unittest.TestCase):
    def test_hash_is_a_sha256_prefix_of_sorted_json(self) -> None:
        program = parse(BASE)
        blob = json.dumps(
            program_to_dict(program),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        self.assertEqual(ast_hash(program), digest[:12])
        self.assertRegex(ast_hash(program), r"^[0-9a-f]{12}$")

    def test_canonical_dict_sorts_declarations_and_fields(self) -> None:
        data = program_to_dict(parse(BASE))
        self.assertEqual(
            [item["name"] for item in data["types"]],
            ["Id", "Money", "Order", "Receipt"],
        )
        order = next(item for item in data["types"] if item["name"] == "Order")
        self.assertEqual(list(order["fields"]), ["id", "total"])
        self.assertEqual(
            [item["name"] for item in data["ops"]],
            ["orders.get", "stripe.refund"],
        )
        self.assertEqual([item["name"] for item in data["flows"]], ["alpha", "beta"])
        effects = next(item for item in data["ops"] if item["name"] == "stripe.refund")
        self.assertEqual(effects["effects"], [("net", "stripe"), ("write", "payments")])

    def test_whitespace_comments_and_order_do_not_change_the_hash(self) -> None:
        expected = ast_hash(parse(BASE))
        self.assertEqual(ast_hash(parse(BASE)), expected)
        self.assertEqual(ast_hash(parse(REORDERED)), expected)
        self.assertEqual(ast_hash(parse(COMMENTED)), expected)
        rewritten = snippet(BASE).replace("\n", "\r\n")
        self.assertEqual(ast_hash(Parser(rewritten).parse()), expected)

    def test_doc_and_body_changes_do_change_the_hash(self) -> None:
        original = ast_hash(parse(BASE))
        changed_doc = ast_hash(parse(BASE.replace('doc "charge"', 'doc "refund"')))
        changed_body = ast_hash(parse(BASE.replace("return receipt", "return order")))
        self.assertNotEqual(changed_doc, original)
        self.assertNotEqual(changed_body, original)
        self.assertNotEqual(changed_doc, changed_body)


if __name__ == "__main__":
    unittest.main()
