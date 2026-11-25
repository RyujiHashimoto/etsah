"""Fill-in templates for a fictional staffing desk.

The templates name the blanks in Japanese. The filled examples use invented
companies, role names, and round amounts. Open legal numbers are not asserted.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import support  # noqa: F401
from etsah import Checker, Parser

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "examples" / "templates"
NAMES = (
    "timesheet.etsah",
    "timesheet_filled.etsah",
    "assignment.etsah",
    "assignment_filled.etsah",
    "bill_rate.etsah",
    "bill_rate_filled.etsah",
)
BLANKS = (
    "timesheet.etsah",
    "assignment.etsah",
    "bill_rate.etsah",
)


def _errors(name: str) -> list:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    return [item for item in Checker(Parser(text).parse()).check() if item.severity == "error"]


class StaffingTemplateTests(unittest.TestCase):
    def test_templates_and_filled_examples_have_no_errors(self) -> None:
        for name in NAMES:
            self.assertEqual(_errors(name), [], name)

    def test_templates_explain_each_blank_and_do_not_state_open_legal_numbers(self) -> None:
        for name in BLANKS:
            text = (TEMPLATES / name).read_text(encoding="utf-8")
            self.assertIn("【空欄】", text, name)
            self.assertIn("各社の規程や専門家に確認", text, name)
        assignment = (TEMPLATES / "assignment.etsah").read_text(encoding="utf-8")
        self.assertNotIn("+ 3", assignment)
        self.assertNotIn("1095", assignment)
        timesheet = (TEMPLATES / "timesheet.etsah").read_text(encoding="utf-8")
        self.assertNotIn("45", timesheet)
        bill = (TEMPLATES / "bill_rate.etsah").read_text(encoding="utf-8")
        self.assertIn("同一労働同一賃金", bill)

    def test_filled_examples_are_obviously_fictional(self) -> None:
        filled = "\n".join((TEMPLATES / name).read_text(encoding="utf-8") for name in NAMES if name.endswith("_filled.etsah"))
        self.assertIn("架空", filled)
        self.assertIn("青葉ブリッジ派遣", filled)
        self.assertIn("2600.00", filled)
        self.assertIn("1750.50", filled)
        self.assertIn("ST-204", filled)
        self.assertNotIn("@", filled)
        self.assertIn("各社の規程や専門家に確認", filled)
