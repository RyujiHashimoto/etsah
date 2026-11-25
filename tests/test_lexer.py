"""Lexer coverage for expression tokens."""
from __future__ import annotations

import unittest

import support  # noqa: F401  (puts the repo root on sys.path)
from etsah import ProutError, tokenize


class TestLexer(unittest.TestCase):
    def test_skips_whitespace_and_appends_eof(self) -> None:
        tokens = tokenize("  \t 1", 4)
        self.assertEqual([t[0] for t in tokens], ["NUMBER", "EOF"])
        self.assertEqual(tokens[-1], ("EOF", "", 4))

    def test_numbers_strings_and_dotted_idents_are_single_tokens(self) -> None:
        tokens = tokenize(r'order.total <= 10 && name != "a\"b"', 3)
        self.assertEqual(
            [(kind, value) for kind, value, _line in tokens if kind != "EOF"],
            [
                ("IDENT", "order.total"),
                ("OP", "<="),
                ("NUMBER", "10"),
                ("OP", "&&"),
                ("IDENT", "name"),
                ("OP", "!="),
                ("STRING", r'"a\"b"'),
            ],
        )
        self.assertTrue(all(line == 3 for _k, _v, line in tokens))

    def test_multi_character_operators_stay_intact(self) -> None:
        values = [value for _k, value, _l in tokenize("<= >= == != && || ->", 1)]
        self.assertEqual(values, ["<=", ">=", "==", "!=", "&&", "||", "->", ""])

    def test_single_character_operators(self) -> None:
        values = [value for _k, value, _l in tokenize("+ - * / < > ! ( ) ,", 1)]
        self.assertEqual(
            [v for v in values if v],
            ["+", "-", "*", "/", "<", ">", "!", "(", ")", ","],
        )

    def test_bool_words_are_idents_until_the_parser(self) -> None:
        kind, value, _line = tokenize("true", 1)[0]
        self.assertEqual((kind, value), ("IDENT", "true"))

    def test_unexpected_character(self) -> None:
        with self.assertRaises(ProutError) as ctx:
            tokenize("1 @ 2", 8)
        self.assertEqual(ctx.exception.line, 8)

    def test_unterminated_string_is_rejected(self) -> None:
        with self.assertRaises(ProutError) as ctx:
            tokenize('"abc', 2)
        self.assertEqual(ctx.exception.line, 2)

    def test_decimal_is_one_token_and_a_trailing_dot_is_not(self) -> None:
        # `1 / 2` is still not folded (see SPEC). A decimal literal is a
        # separate token with exact base-10 digits, not a float.
        kind, value, _line = tokenize("1800.50", 1)[0]
        self.assertEqual((kind, value), ("DECIMAL", "1800.50"))
        self.assertEqual(tokenize("1.2", 1)[0][0], "DECIMAL")
        with self.assertRaises(ProutError):
            tokenize("1.", 1)
        with self.assertRaises(ProutError):
            tokenize("1.2.3", 1)

    def test_iso_date_is_one_token_unless_the_hyphens_are_spaced(self) -> None:
        kind, value, _line = tokenize("2026-11-23", 1)[0]
        self.assertEqual((kind, value), ("DATE", "2026-11-23"))
        spaced = [item[0] for item in tokenize("2026 - 11 - 23", 1) if item[0] != "EOF"]
        self.assertEqual(spaced, ["NUMBER", "OP", "NUMBER", "OP", "NUMBER"])


if __name__ == "__main__":
    unittest.main()
