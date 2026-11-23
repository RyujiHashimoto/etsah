"""36協定 ceilings and automatic 抵触日 calculation.

Date edges live on `staffing_rules` (datetime), not in `prout.py`.
The compiler evaluates spec constants. Runtime checks are described, not run
on live rows.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

import support  # noqa: F401
from proutc import DEFAULT_REGISTRY, cmd_build, load_registry, parse_app, validate
from staffing_rules import (
    MONTHLY_OVERTIME_MAX,
    Span,
    chain_anchor,
    conflict_day,
    cooling_resets,
    format_checks,
    runtime_checks,
    shift_date,
)
from support import PATTERNS, snippet

import argparse


def _diags(text: str, registry=None):
    app = parse_app(snippet(text))
    return validate(app, registry or load_registry(DEFAULT_REGISTRY))


def _codes(diags) -> list[str]:
    return [item.code for item in diags]


def _messages(diags) -> str:
    return " ".join(item.message for item in diags)


class TestConflictDates(unittest.TestCase):
    def test_anniversary_is_the_day_after_three_years(self) -> None:
        self.assertEqual(conflict_day(date(2026, 4, 1)), date(2029, 4, 1))
        self.assertEqual(conflict_day(date(2015, 9, 30)), date(2018, 9, 30))
        self.assertEqual(conflict_day(date(2023, 2, 28)), date(2026, 2, 28))
        self.assertEqual(conflict_day(date(2024, 4, 1)), date(2027, 4, 1))

    def test_feb_29_moves_to_march_1_when_the_anniversary_is_missing(self) -> None:
        self.assertEqual(conflict_day(date(2024, 2, 29)), date(2027, 3, 1))
        self.assertEqual(conflict_day(date(2020, 2, 29)), date(2023, 3, 1))
        # 2028 is a leap year, so a 4-year shift keeps 29 February.
        self.assertEqual(shift_date(date(2024, 2, 29), years=4), date(2028, 2, 29))

    def test_cooling_resets_on_the_day_after_three_months(self) -> None:
        prior = date(2026, 3, 31)
        self.assertFalse(cooling_resets(prior, date(2026, 7, 1)))
        self.assertTrue(cooling_resets(prior, date(2026, 7, 2)))
        self.assertFalse(cooling_resets(prior, date(2026, 4, 1)))
        self.assertFalse(cooling_resets(date(2026, 5, 1), date(2026, 5, 1)))

        # Blank starts 2026-02-01. Three months later is 2026-05-01.
        self.assertFalse(cooling_resets(date(2026, 1, 31), date(2026, 5, 1)))
        self.assertTrue(cooling_resets(date(2026, 1, 31), date(2026, 5, 2)))

        # 2024-11-30 has no anniversary three months later (February has no 30th).
        self.assertFalse(cooling_resets(date(2024, 11, 29), date(2025, 3, 1)))
        self.assertTrue(cooling_resets(date(2024, 11, 29), date(2025, 3, 2)))
        self.assertFalse(cooling_resets(date(2024, 11, 30), date(2025, 3, 1)))
        self.assertTrue(cooling_resets(date(2024, 11, 30), date(2025, 3, 2)))

    def test_chain_anchor_continues_resets_and_keeps_the_earliest_start(self) -> None:
        continued = [
            Span(date(2024, 4, 1), date(2026, 3, 31)),
            Span(date(2026, 4, 1), date(2027, 3, 31), current=True),
        ]
        self.assertEqual(chain_anchor(continued), (date(2024, 4, 1), "continued from 2024-04-01"))

        reset = [
            Span(date(2020, 4, 1), date(2021, 3, 31)),
            Span(date(2021, 7, 2), date(2022, 3, 31), current=True),
        ]
        self.assertEqual(chain_anchor(reset), (date(2021, 7, 2), "cooling reset"))

        alone = [Span(date(2024, 2, 29), date(2027, 2, 28), current=True)]
        self.assertEqual(chain_anchor(alone), (date(2024, 2, 29), "no earlier span"))
        self.assertEqual(conflict_day(chain_anchor(alone)[0]), date(2027, 3, 1))


class TestOvertimeAndConflictPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry(DEFAULT_REGISTRY)

    def test_examples_with_limits_build_and_the_broken_one_fails(self) -> None:
        app = parse_app((PATTERNS / "examples" / "staffing_limits.prout").read_text(encoding="utf-8"))
        self.assertEqual(validate(app, self.registry), [])
        checks = runtime_checks(app)
        text = format_checks(checks)
        self.assertIn("[static]", text)
        self.assertIn("[runtime]", text)
        self.assertIn("2027-03-01", text)
        self.assertIn("cooling reset", text)
        self.assertIn("continued from 2024-04-01", text)
        self.assertIn("notify", text)
        self.assertIn("reject", text)
        self.assertTrue(any(item["phase"] == "static" for item in checks))
        self.assertTrue(any(item["phase"] == "runtime" for item in checks))

        broken = parse_app(
            (PATTERNS / "examples" / "staffing_limits_broken.prout").read_text(encoding="utf-8")
        )
        diags = validate(broken, self.registry)
        self.assertTrue(any(item.severity == "error" for item in diags))
        messages = _messages(diags)
        self.assertIn("50", messages)
        self.assertIn("721", messages)
        self.assertIn("100", messages)
        self.assertIn("7", messages)
        self.assertIn("36協定", messages)
        self.assertIn("2027-04-02", messages)
        self.assertIn("2027-04-01", messages)
        self.assertIn("2027-03-02", messages)
        self.assertTrue(any(item.code == "W-POLICY" and "disagrees" in item.message for item in diags))

    def test_missing_agreement_and_ceilings(self) -> None:
        missing = _diags(
            """
            app demo
              stack nextjs
              entity Timesheet
                id: Id
                work_date: Date
                hours: Duration
                overtime_hours: Duration
                status: String
              pattern timesheet
                entity: Timesheet
                approver: client_approver
            """,
            self.registry,
        )
        self.assertIn("E-POLICY", _codes(missing))
        self.assertIn("36協定", _messages(missing))

        over = _diags(self._agreement("monthly_overtime_hours: 46"), self.registry)
        self.assertIn("exceeds the general statutory maximum of 45", _messages(over))

        at_cap = _diags(self._agreement("monthly_overtime_hours: 45"), self.registry)
        self.assertEqual(at_cap, [])

        special_year = _diags(
            self._agreement(
                """
                special_clause: enabled
                special_yearly_overtime_hours: 721
                """,
                holiday=True,
            ),
            self.registry,
        )
        self.assertIn("721", _messages(special_year))
        self.assertIn("720", _messages(special_year))

        month = _diags(
            self._agreement(
                """
                special_clause: enabled
                month_with_holiday_hours: 100
                """,
                holiday=True,
            ),
            self.registry,
        )
        self.assertIn("under 100", _messages(month))

        month_ok = _diags(
            self._agreement(
                """
                special_clause: enabled
                month_with_holiday_hours: 99.5
                """,
                holiday=True,
            ),
            self.registry,
        )
        self.assertEqual(month_ok, [])

        average = _diags(
            self._agreement(
                """
                special_clause: enabled
                average_hours: 80.1
                """,
                holiday=True,
            ),
            self.registry,
        )
        self.assertIn("80.1", _messages(average))

        months = _diags(
            self._agreement(
                """
                special_clause: enabled
                months_over_limit: 7
                """,
                holiday=True,
            ),
            self.registry,
        )
        self.assertIn("7", _messages(months))
        self.assertEqual(
            _diags(
                self._agreement(
                    """
                    special_clause: enabled
                    months_over_limit: 6
                    """,
                    holiday=True,
                ),
                self.registry,
            ),
            [],
        )

        premature = _diags(
            self._agreement("special_yearly_overtime_hours: 720"),
            self.registry,
        )
        self.assertIn("special_clause: enabled", _messages(premature))

        warn = _diags(self._agreement("warn_at_hours: 45"), self.registry)
        self.assertIn("warning threshold", _messages(warn))

    def test_omitted_special_month_uses_under_100_at_runtime(self) -> None:
        app = parse_app(
            snippet(
                self._agreement(
                    """
                    special_clause: enabled
                    """,
                    holiday=True,
                )
            )
        )
        self.assertEqual(validate(app, self.registry), [])
        text = format_checks(runtime_checks(app))
        self.assertIn("under 100", text)
        self.assertIn("[runtime]", text)
        self.assertIn("notify", text)
        self.assertIn("reject", text)
        self.assertIn("2, 3, 4, 5, or 6 month", text)

    def test_notified_conflict_date_disagrees_without_failing_the_term(self) -> None:
        diags = _diags(
            self._contract(
                """
                start_date: 2026-04-01
                end_date: 2027-03-31
                conflict_date: 2029-05-01
                """
            ),
            self.registry,
        )
        self.assertEqual(_codes(diags), ["W-POLICY"])
        self.assertIn("2029-05-01", diags[0].message)
        self.assertIn("2029-04-01", diags[0].message)
        self.assertIn("not a legal opinion", diags[0].message)

    def test_continuous_history_rejects_an_end_the_notified_date_would_allow(self) -> None:
        diags = _diags(
            self._contract(
                """
                start_date: 2026-04-01
                end_date: 2027-04-02
                conflict_date: 2029-04-01
                office: tokyo-hq
                org_unit: general
                person: staff-a
                office_history: 2024-04-01/2026-03-31
                """
            ),
            self.registry,
        )
        errors = [item for item in diags if item.severity == "error"]
        self.assertTrue(errors)
        self.assertIn("2027-04-02", errors[0].message)
        self.assertIn("2027-04-01", errors[0].message)
        self.assertIn("continued from 2024-04-01", errors[0].message)
        self.assertTrue(any(item.code == "W-POLICY" for item in diags))

    def test_cooling_reset_and_leap_day_terms(self) -> None:
        cooled = _diags(
            self._contract(
                """
                start_date: 2021-07-02
                end_date: 2022-03-31
                conflict_date: 2024-07-02
                office: osaka
                org_unit: sales
                person: p1
                office_history: 2020-04-01/2021-03-31
                """
            ),
            self.registry,
        )
        self.assertEqual(cooled, [])

        still_open = _diags(
            self._contract(
                """
                start_date: 2021-07-01
                end_date: 2022-03-31
                conflict_date: 2023-04-01
                office: osaka
                org_unit: sales
                person: p1
                office_history: 2020-04-01/2021-03-31
                """
            ),
            self.registry,
        )
        self.assertEqual(still_open, [], _messages(still_open))

        leap_ok = _diags(
            self._contract(
                """
                start_date: 2024-02-29
                end_date: 2027-03-01
                conflict_date: 2027-03-01
                """
            ),
            self.registry,
        )
        self.assertEqual(leap_ok, [])

        leap_after = _diags(
            self._contract(
                """
                start_date: 2024-02-29
                end_date: 2027-03-02
                conflict_date: 2027-03-01
                """
            ),
            self.registry,
        )
        self.assertTrue(any(item.code == "E-POLICY" and "抵触日" in item.message for item in leap_after))

    def test_optional_exemption_downgrades_only_the_calculated_overrun(self) -> None:
        diags = _diags(
            self._contract(
                """
                start_date: 2026-04-01
                end_date: 2027-04-02
                conflict_date: 2029-04-01
                exempt: day_limited
                office: tokyo-hq
                org_unit: general
                person: staff-a
                office_history: 2024-04-01/2026-03-31
                """
            ),
            self.registry,
        )
        self.assertTrue(diags)
        self.assertTrue(all(item.severity == "warning" for item in diags))
        text = _messages(diags)
        self.assertIn("day_limited", text)
        self.assertIn("not a legal opinion", text)
        self.assertNotIn("E-POLICY", _codes(diags))

    def test_personal_history_can_bind_before_the_office_date(self) -> None:
        diags = _diags(
            self._contract(
                """
                start_date: 2026-04-01
                end_date: 2027-06-02
                conflict_date: 2029-04-01
                office: tokyo-hq
                org_unit: general
                person: staff-a
                person_history: 2024-06-01/2026-03-31
                """
            ),
            self.registry,
        )
        errors = [item for item in diags if item.severity == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("個人単位", errors[0].message)
        self.assertIn("2027-06-01", errors[0].message)

    def test_sibling_contracts_share_an_office_anchor(self) -> None:
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
                start_date: 2024-04-01
                end_date: 2025-03-31
                conflict_date: 2027-04-01
                office: hq
                org_unit: sales
                person: a
              pattern dispatch_contract
                entity: DispatchContract
                staff: Staff
                client: Client
                start_date: 2025-04-01
                end_date: 2026-03-31
                conflict_date: 2027-04-01
                office: hq
                org_unit: sales
                person: b
            """,
            self.registry,
        )
        self.assertEqual(diags, [], _messages(diags))

    def test_half_specified_person_key_and_bad_history(self) -> None:
        half = _diags(
            self._contract(
                """
                start_date: 2026-04-01
                end_date: 2027-03-31
                conflict_date: 2029-04-01
                person: staff-a
                """
            ),
            self.registry,
        )
        self.assertTrue(any(item.code == "E-SLOT" and "org_unit" in item.message for item in half))

        bad = _diags(
            self._contract(
                """
                start_date: 2026-04-01
                end_date: 2027-03-31
                conflict_date: 2029-04-01
                office_history: 2024/04/01-2026/03/31
                """
            ),
            self.registry,
        )
        self.assertTrue(any(item.code == "E-SLOT" and "office_history" in item.message for item in bad))

    def test_build_writes_static_and_runtime_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "limits.prout"
            path.write_text(
                (PATTERNS / "examples" / "staffing_limits.prout").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            out = Path(tmp) / "out"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = cmd_build(
                    argparse.Namespace(file=str(path), registry=str(DEFAULT_REGISTRY), out=str(out))
                )
            self.assertEqual(code, 0)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            phases = {item["phase"] for item in manifest["runtime_checks"]}
            self.assertEqual(phases, {"static", "runtime"})
            report = (out / "report.html").read_text(encoding="utf-8")
            self.assertIn("lib/labor/conflict_date.ts", report)
            self.assertIn("lib/labor/overtime_checks.ts", report)

    def test_manifest_defaults_match_the_general_rule(self) -> None:
        slots = self.registry["overtime_agreement"].slots
        self.assertEqual(slots["monthly_overtime_hours"]["default"], str(MONTHLY_OVERTIME_MAX))
        self.assertEqual(slots["yearly_overtime_hours"]["default"], "360")
        self.assertEqual(slots["special_yearly_overtime_hours"]["default"], "720")
        self.assertEqual(slots["average_hours"]["default"], "80")
        self.assertEqual(slots["months_over_limit"]["default"], "6")
        self.assertEqual(Decimal(slots["monthly_overtime_hours"]["default"]), MONTHLY_OVERTIME_MAX)

    def _agreement(self, extra: str, holiday: bool = False) -> str:
        holiday_field = "\n                holiday_hours: Duration" if holiday else ""
        extra_lines = "\n".join(
            "                " + line.strip() for line in extra.strip().splitlines() if line.strip()
        )
        return f"""
            app demo
              stack nextjs
              entity Timesheet
                id: Id
                work_date: Date
                hours: Duration
                overtime_hours: Duration{holiday_field}
                status: String
              entity OvertimeAgreement
                id: Id
                status: String
              pattern timesheet
                entity: Timesheet
                approver: client_approver
                period: monthly
                overtime_cap_hours: 45
              pattern overtime_agreement
                entity: OvertimeAgreement
                timesheet: Timesheet
                warn_at_hours: 30
                on_warning: notify
                on_limit: reject
{extra_lines}
            """

    def _contract(self, slots: str) -> str:
        slot_lines = "\n".join(
            "                " + line.strip() for line in slots.strip().splitlines() if line.strip()
        )
        return f"""
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
{slot_lines}
            """


if __name__ == "__main__":
    unittest.main()
