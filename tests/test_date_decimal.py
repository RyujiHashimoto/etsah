"""Date and Decimal in the contract layer.

Integer programs keep their AST hash. Division stays undecided. Decimal
arithmetic is exact, and a date literal is a calendar day rather than
subtraction.
"""
from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

import support  # noqa: F401
from prout import Checker, Parser, ProutError, ast_hash, parse_expr, program_to_dict
from support import ROOT, check, codes, snippet


def _typed(expr: str, ret: str = "Bool") -> list:
    return check(
        f"""
        type Money
        flow f(i: Int, d: Decimal, day: Date, other: Date, m: Money, s: String) -> {ret}
          return {expr}
        """
    )


class TestDateDecimalSyntax(unittest.TestCase):
    def test_literals_and_exact_decimal_equality(self) -> None:
        decimal = parse_expr("1800.50", 1)
        self.assertEqual(decimal.name, "dec")
        self.assertEqual(decimal.value, Decimal("1800.50"))
        self.assertEqual(parse_expr("1800.50 == 1800.5", 1).kind, "bin")
        parsed = parse_expr("2026-11-23", 1)
        self.assertEqual(parsed.name, "date")
        self.assertEqual(parsed.value, date(2026, 11, 23))

    def test_invalid_calendar_day_is_rejected(self) -> None:
        with self.assertRaises(ProutError) as ctx:
            parse_expr("2026-02-31", 4)
        self.assertIn("invalid date", str(ctx.exception))
        self.assertEqual(ctx.exception.line, 4)
        with self.assertRaises(ProutError):
            parse_expr("2026-02-29", 1)
        leap = parse_expr("2024-02-29", 1)
        self.assertEqual(leap.value, date(2024, 2, 29))

    def test_spaced_hyphens_stay_integer_subtraction(self) -> None:
        diags = _typed("2026 - 11 - 23", "Int")
        self.assertEqual(diags, [])

    def test_ast_stores_json_safe_spellings(self) -> None:
        program = Parser(snippet("op f() -> Decimal\n  impl 1800.50\n")).parse()
        blob = program_to_dict(program)
        self.assertEqual(blob["ops"][0]["impl"]["value"], "1800.50")
        dated = Parser(snippet("op f() -> Date\n  impl 2026-11-23\n")).parse()
        self.assertEqual(program_to_dict(dated)["ops"][0]["impl"]["value"], "2026-11-23")


class TestDateDecimalTypes(unittest.TestCase):
    def test_arithmetic_and_comparison(self) -> None:
        self.assertEqual(_typed("day + 3", "Date"), [])
        self.assertEqual(_typed("3 + day", "Date"), [])
        self.assertEqual(_typed("day - 3", "Date"), [])
        self.assertEqual(_typed("day - other", "Int"), [])
        self.assertEqual(_typed("d + i", "Decimal"), [])
        self.assertEqual(_typed("i + d", "Decimal"), [])
        self.assertEqual(_typed("d - i", "Decimal"), [])
        self.assertEqual(_typed("i * d", "Decimal"), [])
        self.assertEqual(_typed("d / 2", "Decimal"), [])
        self.assertEqual(_typed("d >= i"), [])
        self.assertEqual(_typed("day <= other"), [])
        self.assertEqual(_typed("d == 1800"), [])

    def test_date_does_not_mix_with_decimal_or_money(self) -> None:
        for expr in ("day + d", "day - d", "d - day", "day * i", "day <= d", "day <= i", "m + d", "d * m"):
            with self.subTest(expr=expr):
                self.assertIn("E-TYPE", codes(_typed(expr), "error"), expr)

    def test_int_int_division_stays_int(self) -> None:
        self.assertEqual(_typed("i / 2", "Int"), [])


class TestDateDecimalProofs(unittest.TestCase):
    def test_constant_decimal_and_date_pres_are_decided(self) -> None:
        clean = check(
            """
            op rates(bill: Decimal, pay: Decimal) -> Int
              pre bill >= pay
              pre 0.1 + 0.2 == 0.3
              impl native
            op term(end: Date, conflict: Date) -> Int
              pre end <= conflict
              pre (2026-04-11 - 2026-04-01) == 10
              impl native
            flow ok() -> Int
              let rates_ok = rates(1800.50, 1800)
              let term_ok = term(2026-04-01 + 10, 2026-04-11)
              return rates_ok
            """
        )
        self.assertEqual(clean, [])

        false_rate = check(
            """
            op rates(bill: Decimal, pay: Decimal) -> Int
              pre bill >= pay
              impl native
            flow bad() -> Int
              return rates(1500.5, 1800.50)
            """
        )
        self.assertEqual(codes(false_rate, "error"), ["E-PRE"])
        self.assertIn("1500.5 >= 1800.50", false_rate[0].message)

        false_date = check(
            """
            op term(end: Date, conflict: Date) -> Int
              pre end <= conflict
              impl native
            flow bad() -> Int
              return term(2029-04-02, 2029-04-01)
            """
        )
        self.assertEqual(codes(false_date, "error"), ["E-PRE"])
        self.assertIn("2029-04-02 <= 2029-04-01", false_date[0].message)

    def test_division_is_still_not_constant_folded(self) -> None:
        diags = check(
            """
            op f() -> Int
              pre 1 / 2 <= 1
              pre 1.0 / 2.0 <= 1
              impl native
            flow g() -> Int
              return f()
            """
        )
        self.assertEqual(codes(diags, "error"), [])
        self.assertEqual(codes(diags, "warning"), ["W-PRE", "W-PRE"])

    def test_variable_date_and_decimal_stay_runtime_guards(self) -> None:
        diags = check(
            """
            op rates(bill: Decimal, pay: Decimal) -> Int
              pre bill >= pay
              impl native
            op term(end: Date, conflict: Date) -> Int
              pre end <= conflict
              impl native
            flow g(bill: Decimal, pay: Decimal, end: Date, conflict: Date) -> Int
              let rates_ok = rates(bill, pay)
              let term_ok = term(end, conflict)
              return rates_ok
            """
        )
        self.assertEqual(codes(diags, "error"), [])
        self.assertEqual(codes(diags, "warning"), ["W-PRE", "W-PRE"])
        self.assertTrue(all("runtime guard" in item.message for item in diags))


class TestExistingHashes(unittest.TestCase):
    def test_sample_hashes_do_not_change(self) -> None:
        # These match origin/main after pre/post canonicalization. Date and
        # Decimal literals are absent from these files, so their hashes stay put.
        expected = {
            "refund_broken.prout": "c0ee67d7cba1",
            "refund_fixed.prout": "bae8501e8b3f",
            "staffing.prout": "afd29e7ccdd0",
            "staffing_broken.prout": "afbf07d01bd2",
        }
        for name, digest in expected.items():
            program = Parser((ROOT / "examples" / name).read_text(encoding="utf-8")).parse()
            self.assertEqual(ast_hash(program), digest, name)


class TestStaffingDateSamples(unittest.TestCase):
    def test_dates_sample_checks_and_broken_sample_fails(self) -> None:
        good = Parser((ROOT / "examples" / "staffing_dates.prout").read_text(encoding="utf-8")).parse()
        diags = Checker(good).check()
        self.assertEqual(codes(diags, "error"), [])
        self.assertEqual(codes(diags, "warning"), ["W-PRE", "W-PRE", "W-PRE", "W-PRE"])

        broken = Parser((ROOT / "examples" / "staffing_dates_broken.prout").read_text(encoding="utf-8")).parse()
        failed = Checker(broken).check()
        self.assertEqual(codes(failed, "error"), ["E-PRE", "E-PRE", "E-PRE"])
        text = " ".join(item.message for item in failed)
        self.assertIn("1500.5 >= 1800.50", text)
        self.assertIn("2029-04-02 <= 2029-04-01", text)
        self.assertIn("false == true", text)


if __name__ == "__main__":
    unittest.main()
