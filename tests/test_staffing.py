"""Staffing-agency patterns and the contract-layer examples around them.

Dates, durations, and decimal hourly rates live in the pattern compiler.
The contract layer is unchanged: rates and calendar days are integers there,
and a field comparison stays a runtime guard.
"""
from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

import support  # noqa: F401
from prout import Checker, Parser
from proutc import (
    DEFAULT_REGISTRY,
    find_patterns,
    load_registry,
    parse_app,
    parse_date,
    parse_duration,
    parse_rate,
    validate,
)
from support import ROOT, PATTERNS, codes, snippet


def _diags(text: str, registry=None):
    app = parse_app(snippet(text))
    return validate(app, registry or load_registry(DEFAULT_REGISTRY))


def _codes(diags, code: str | None = None) -> list[str]:
    found = [item.code for item in diags]
    if code is None:
        return found
    return [item for item in found if item == code]


class TestMeasures(unittest.TestCase):
    def test_dates_are_real_calendar_days(self) -> None:
        self.assertEqual(parse_date("2026-04-01"), date(2026, 4, 1))
        self.assertEqual(parse_date(" 2026-04-01 "), date(2026, 4, 1))
        self.assertIsNone(parse_date("2026-4-1"))
        self.assertIsNone(parse_date("2026-02-31"))
        self.assertIsNone(parse_date("2026/04/01"))

    def test_rates_are_non_negative_decimals(self) -> None:
        self.assertEqual(parse_rate("1800.50"), Decimal("1800.50"))
        self.assertEqual(parse_rate("1800"), Decimal("1800"))
        self.assertIsNone(parse_rate("-1"))
        self.assertIsNone(parse_rate("1e2"))
        self.assertIsNone(parse_rate("1800."))
        self.assertIsNone(parse_rate(".5"))

    def test_durations_accept_decimals_and_clocks(self) -> None:
        self.assertEqual(parse_duration("7.5"), Decimal("7.5"))
        self.assertEqual(parse_duration("7h30m"), Decimal("7.5"))
        self.assertEqual(parse_duration("0"), Decimal("0"))
        self.assertIsNone(parse_duration("7h60m"))
        self.assertIsNone(parse_duration("-1"))
        self.assertIsNone(parse_duration("7h"))


class TestStaffingPatterns(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry(DEFAULT_REGISTRY)

    def test_examples_build_and_the_broken_one_is_caught(self) -> None:
        for name in ("staffing.prout", "staffing_desk.prout"):
            app = parse_app((PATTERNS / "examples" / name).read_text(encoding="utf-8"))
            self.assertEqual(validate(app, self.registry), [], name)

        broken = parse_app((PATTERNS / "examples" / "staffing_broken.prout").read_text(encoding="utf-8"))
        diags = validate(broken, self.registry)
        self.assertEqual(_codes(diags), ["E-POLICY", "E-SLOT", "E-POLICY", "E-POLICY"])
        text = " ".join(item.message for item in diags)
        self.assertIn("抵触日", text)
        self.assertIn("2029-04-02 <= 2029-04-01", text)
        self.assertIn("approver", text)
        self.assertIn("1500 >= 1800.50", text)
        self.assertIn("36協定", text)

    def test_existing_apps_still_validate(self) -> None:
        for path in (
            PATTERNS / "examples" / "shop.prout",
            PATTERNS / "examples" / "expense.prout",
            PATTERNS / "examples" / "travel_request.prout",
            PATTERNS / "examples" / "equipment_purchase.prout",
            PATTERNS / "bench" / "pc_lending.prout",
        ):
            app = parse_app(path.read_text(encoding="utf-8"))
            errors = [item for item in validate(app, self.registry) if item.severity == "error"]
            self.assertEqual(errors, [], path.name)

    def test_equal_rates_and_equal_conflict_date_pass(self) -> None:
        diags = _diags(
            """
            app demo
              stack nextjs
              entity Timesheet
                id: Id
                work_date: Date
                hours: Duration
                status: String
              entity Invoice
                id: Id
                bill_rate: Rate
                pay_rate: Rate
                hours: Duration
              entity Staff
                id: Id
              entity Client
                id: Id
              entity DispatchContract
                id: Id
                duties: String
                workplace: String
                start_date: Date
                end_date: Date
                conflict_date: Date
                status: String
              entity OvertimeAgreement
                id: Id
                status: String
              pattern timesheet
                entity: Timesheet
                approver: client_approver
                overtime_cap_hours: 7h30m
              pattern overtime_agreement
                entity: OvertimeAgreement
                timesheet: Timesheet
                warn_at_hours: 4
                on_warning: notify
                on_limit: reject
              pattern staffing_billing
                entity: Invoice
                timesheet: Timesheet
                bill_rate: 1800.50
                pay_rate: 1800.5
              pattern dispatch_contract
                entity: DispatchContract
                staff: Staff
                client: Client
                start_date: 2026-04-01
                end_date: 2029-04-01
                conflict_date: 2029-04-01
            """,
            self.registry,
        )
        self.assertEqual(diags, [])

    def test_bad_measure_field_and_missing_approval(self) -> None:
        diags = _diags(
            """
            app demo
              stack nextjs
              entity Timesheet
                id: Id
                work_date: String
                status: String
              entity Invoice
                id: Id
                bill_rate: Rate
                pay_rate: Rate
              pattern staffing_billing
                entity: Invoice
                timesheet: Timesheet
                bill_rate: 2000
                pay_rate: 1500
            """,
            self.registry,
        )
        self.assertIn("E-POLICY", _codes(diags))
        self.assertTrue(any("timesheet" in item.message and "approval" in item.message for item in diags))
        self.assertTrue(any(item.code == "E-ENTITY" and "hours" in item.message for item in diags))

        mismatched = _diags(
            """
            app demo
              stack nextjs
              entity Timesheet
                id: Id
                work_date: String
                hours: Duration
                status: String
              pattern timesheet
                entity: Timesheet
                approver: client_approver
                period: weekly
                overtime_cap_hours: -1
            """,
            self.registry,
        )
        self.assertTrue(any(item.code == "E-TYPE" and "Date" in item.message for item in mismatched))
        self.assertTrue(any(item.code == "E-SLOT" and "weekly" in item.message for item in mismatched))
        self.assertTrue(any(item.code == "E-SLOT" and "duration" in item.message for item in mismatched))

    def test_exempt_conflict_is_a_warning_not_a_legal_opinion(self) -> None:
        diags = _diags(
            """
            app demo
              stack nextjs
              entity Staff
                id: Id
              entity Client
                id: Id
              entity DispatchContract
                id: Id
                duties: String
                workplace: String
                start_date: Date
                end_date: Date
                conflict_date: Date
                status: String
              pattern dispatch_contract
                entity: DispatchContract
                staff: Staff
                client: Client
                start_date: 2026-04-01
                end_date: 2029-04-02
                conflict_date: 2029-04-01
                exempt: indefinite
            """,
            self.registry,
        )
        self.assertEqual(_codes(diags), ["W-POLICY"])
        self.assertIn("not a legal opinion", diags[0].message)
        self.assertEqual(diags[0].severity, "warning")

    def test_start_after_end_and_missing_skill_field(self) -> None:
        diags = _diags(
            """
            app demo
              stack nextjs
              entity Staff
                id: Id
                name: String
              entity Client
                id: Id
              entity Opening
                id: Id
                title: String
              entity DispatchContract
                id: Id
                duties: String
                workplace: String
                start_date: Date
                end_date: Date
                conflict_date: Date
                status: String
              pattern dispatch_contract
                entity: DispatchContract
                staff: Staff
                client: Client
                start_date: 2027-04-01
                end_date: 2026-04-01
                conflict_date: 2029-04-01
              pattern matching
                staff: Staff
                opening: Opening
                skills: skills
            """,
            self.registry,
        )
        text = " ".join(item.message for item in diags)
        self.assertIn("start_date must be on or before end_date", text)
        self.assertIn("skills", text)
        self.assertTrue(any(item.code == "E-ENTITY" and "required_skills" in item.message for item in diags))

    def test_find_discovers_the_new_patterns_without_displacing_approval(self) -> None:
        found = find_patterns("申請 承認", self.registry)
        self.assertEqual(found[0][1].name, "approval")
        self.assertEqual(found[0][0], 15)
        self.assertEqual(find_patterns("勤怠", self.registry)[0][1].name, "timesheet")
        self.assertEqual(find_patterns("抵触日", self.registry)[0][1].name, "dispatch_contract")
        self.assertEqual(find_patterns("請求 給与", self.registry)[0][1].name, "staffing_billing")
        self.assertEqual(find_patterns("マッチング", self.registry)[0][1].name, "matching")

    def test_manifest_notes_keep_the_legal_caveats(self) -> None:
        billing = " ".join(self.registry["staffing_billing"].notes)
        self.assertIn("同一労働同一賃金", billing)
        term = " ".join(self.registry["dispatch_contract"].notes)
        self.assertIn("抵触日", term)
        self.assertIn("3年", term)
        cap = self.registry["timesheet"].constraints[0]["note"]
        self.assertIn("36協定", cap)
        self.assertIn("45", cap)


class TestStaffingContract(unittest.TestCase):
    def test_constant_case_is_proved_and_stored_case_is_a_guard(self) -> None:
        program = Parser((ROOT / "examples" / "staffing.prout").read_text(encoding="utf-8")).parse()
        diags = Checker(program).check()
        self.assertEqual(codes(diags, "error"), [])
        self.assertEqual(codes(diags, "warning"), ["W-PRE", "W-PRE", "W-PRE"])
        messages = " ".join(item.message for item in diags)
        self.assertIn("contract.bill_yen >= contract.pay_yen", messages)
        self.assertIn("contract.end_day <= contract.conflict_day", messages)
        self.assertIn("contract.client_approved == true", messages)

    def test_broken_contract_fails_rate_term_and_approval(self) -> None:
        program = Parser((ROOT / "examples" / "staffing_broken.prout").read_text(encoding="utf-8")).parse()
        diags = Checker(program).check()
        self.assertEqual(codes(diags, "error"), ["E-PRE", "E-PRE", "E-PRE"])
        messages = [item.message for item in diags]
        self.assertIn("1500 >= 1800", messages[0])
        self.assertIn("21641 <= 21640", messages[1])
        self.assertIn("false == true", messages[2])


if __name__ == "__main__":
    unittest.main()
