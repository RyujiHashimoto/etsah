"""The diagnostic table lists exactly the codes the checkers already emit."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CONTRACT = {
    "E-PARSE",
    "E-LABEL",
    "E-DUP",
    "E-TYPE",
    "E-UNDEF",
    "E-FIELD",
    "E-ARITY",
    "E-DECLASS",
    "W-DECLASS",
    "E-EFF",
    "E-FLOW",
    "E-PRE",
    "W-PRE",
    "E-IFLOW-CONTRACT",
    "E-IFLOW-RET",
    "E-IFLOW-ARG",
    "E-IFLOW-SINK",
    "E-SINK",
    "E-SINK-UNBOUND",
    "E-POLICY",
    "W-BUDGET",
}
PATTERN = {
    "E-ENTITY",
    "E-TYPE",
    "E-TARGET",
    "E-SLOT",
    "E-POLICY",
    "W-POLICY",
    "E-HOOK",
    "E-STACK",
    "E-FREEDOM",
    "E-DUP",
    "E-PATTERN",
    "W-SLOT",
    "E-CONFLICT",
    "E-CUSTOM",
    "E-SCENE",
    "E-HOOK-ARG",
}
EXPECTED = CONTRACT | PATTERN
ROW = re.compile(r"^\|\s*`((?:E|W)-[A-Z0-9-]+)`\s*\|\s*([^|]+?)\s*\|", re.M)


class DiagnosticsDocTests(unittest.TestCase):
    def test_table_lists_each_emitted_code_once(self) -> None:
        text = (ROOT / "docs" / "diagnostics.md").read_text(encoding="utf-8")
        rows = ROW.findall(text)
        codes = [code for code, _layer in rows]
        layers = {code: layer for code, layer in rows}
        self.assertEqual(set(codes), EXPECTED)
        self.assertEqual(len(codes), len(set(codes)))
        self.assertIn("SPEC.md", text)
        self.assertIn("SPEC.md` wins", text)
        self.assertIn("does not change checker behavior", text)
        for code in ("E-DUP", "E-TYPE", "E-POLICY"):
            self.assertEqual(layers[code], "both")
        for code in CONTRACT - {"E-DUP", "E-TYPE", "E-POLICY"}:
            self.assertEqual(layers[code], "contract", code)
        for code in PATTERN - {"E-DUP", "E-TYPE", "E-POLICY"}:
            self.assertEqual(layers[code], "pattern", code)
        etsah = (ROOT / "etsah.py").read_text(encoding="utf-8")
        etsahc = (ROOT / "patterns" / "etsahc.py").read_text(encoding="utf-8")
        for code in EXPECTED:
            quoted = f'"{code}"'
            self.assertTrue(quoted in etsah or quoted in etsahc, code)

    def test_indexes_link_the_table_and_drop_the_frozen_total(self) -> None:
        llms = (ROOT / "llms.txt").read_text(encoding="utf-8")
        quick = (ROOT / "docs" / "en" / "agents-quickstart.md").read_text(encoding="utf-8")
        self.assertIn("docs/diagnostics.md", llms)
        self.assertIn("docs/diagnostics.md", quick)
        self.assertNotIn("365 tests", llms)


if __name__ == "__main__":
    unittest.main()
