"""A parent's declared limit bounds the sum of its child tasks.

Phase 1 is static. These tests do not run the guard and do not share a ledger.
The desk, the tickets, and the amounts are fictional.
"""
from __future__ import annotations

import unittest

import support  # noqa: F401
from support import check


def _calls(left: int, right: int, parent: int = 10) -> str:
    return f"""
        type Id
        op desk.read(ticket: Id) -> Bool
          doc "Read fictional ticket N-4402"
          eff read(tickets)
          impl native
        agent north_pier
          doc "Fictional North Pier desk"
          allow read(tickets)
          budget run 0 period 0
          limit net 0 hour
          limit emit 0 hour
          limit calls {parent} period day
          audit ast_hash
        task sort_ticket(ticket: Id) -> Bool
          agent north_pier
          doc "Sort ticket N-4402"
          trigger webhook "north-pier"
          eff read(tickets)
          key ticket
          retry 0 escalate clerk
          limit calls {left} period day
          let seen = desk.read(ticket)
          return seen
        task file_ticket(ticket: Id) -> Bool
          agent north_pier
          doc "File ticket N-4402"
          trigger webhook "north-pier"
          eff read(tickets)
          key ticket
          retry 0 escalate clerk
          limit calls {right} period day
          let seen = desk.read(ticket)
          return seen
    """


def _spend(left: str, right: str, parent: str = "100.00", right_currency: str = "JPY") -> str:
    return f"""
        type Id
        op payments.charge(amount: Decimal) -> Bool
          doc "Charge fictional account A-19"
          eff spend(payments)
          spend from amount currency JPY
          impl native
        policy
          payment spend(payments)
        agent north_pier
          doc "Fictional North Pier desk"
          allow spend(payments)
          budget run 1000 period 1000
          limit net 0 hour
          limit emit 0 hour
          limit spend {parent} JPY period day
          audit ast_hash
        gate approve_charge
          approver billing_lead
          before spend(payments)
          on_approved payments.charge
        task charge_east(amount: Decimal) -> Bool
          agent north_pier
          doc "Charge the east folio of account A-19"
          trigger webhook "north-pier-east"
          eff spend(payments)
          gate approve_charge
          limit spend {left} JPY period day
          key amount
          retry 0 escalate billing_lead
          let charged = payments.charge(amount)
          return charged
        task charge_west(amount: Decimal) -> Bool
          agent north_pier
          doc "Charge the west folio of account A-19"
          trigger webhook "north-pier-west"
          eff spend(payments)
          gate approve_charge
          limit spend {right} {right_currency} period day
          key amount
          retry 0 escalate billing_lead
          let charged = payments.charge(amount)
          return charged
    """


def _messages(source: str) -> list[str]:
    return [item.message for item in check(source) if item.severity == "error"]


class ParentBudgetTests(unittest.TestCase):
    def test_call_limits_that_sum_over_the_parent_are_e_policy(self) -> None:
        found = [item for item in check(_calls(6, 6)) if item.severity == "error"]
        self.assertTrue(
            any(item.code == "E-POLICY" and "sum to 12" in item.message and "10 per day" in item.message for item in found)
        )

    def test_call_limits_that_fit_under_the_parent_pass(self) -> None:
        self.assertEqual(_messages(_calls(4, 4)), [])

    def test_spend_limits_that_sum_over_the_parent_are_e_policy(self) -> None:
        found = [item for item in check(_spend("60.00", "60.00")) if item.severity == "error"]
        self.assertTrue(
            any(
                item.code == "E-POLICY" and "sum to 120.00" in item.message and "100.00" in item.message and "JPY" in item.message
                for item in found
            )
        )
        self.assertTrue(all(item.code == "E-POLICY" for item in found))

    def test_spend_limits_that_fit_under_the_parent_pass(self) -> None:
        self.assertEqual(_messages(_spend("40.00", "40.00")), [])

    def test_a_different_currency_is_not_added_into_the_parent_sum(self) -> None:
        messages = _messages(_spend("40.00", "40.00", right_currency="USD"))
        self.assertTrue(any("USD" in message and "JPY" in message for message in messages))
        self.assertFalse(any("sum to" in message for message in messages))
