#!/usr/bin/env python3
"""Regression tests for the PROUT Patterns compiler (patterns/proutc.py).

Run from the repository root:

    python3 -m unittest discover -s tests -v

Covers duplicate pattern application, natural-language pattern
discovery, and the enforced `field_types` manifest key.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "patterns"))

from proutc import (  # noqa: E402
    DEFAULT_REGISTRY,
    find_patterns,
    load_registry,
    manifest_from_dict,
    parse_app,
    validate,
)

REGISTRY = load_registry(DEFAULT_REGISTRY)


def error_codes(src: str, registry=None) -> list[str]:
    app = parse_app(src)
    diags = validate(app, registry or REGISTRY)
    return [d.code for d in diags if d.severity == "error"]


class DuplicateApplication(unittest.TestCase):
    def test_same_pattern_same_entity_is_a_conflict(self):
        codes = error_codes(
            """
app dup_test
  stack nextjs
  freedom guided
  entity Item
    id: Id
    name: String
  pattern crud Item
    entity: Item
  pattern crud Item
    entity: Item
"""
        )
        self.assertIn("E-CONFLICT", codes)

    def test_same_pattern_different_entities_is_allowed(self):
        codes = error_codes(
            """
app two_entities
  stack nextjs
  freedom guided
  entity Item
    id: Id
    name: String
  entity Order
    id: Id
    name: String
  pattern crud Item
    entity: Item
  pattern crud Order
    entity: Order
"""
        )
        self.assertNotIn("E-CONFLICT", codes)

    def test_named_instances_on_one_entity_are_allowed(self):
        # ai_task / approval_gate uses carry an instance name and several
        # distinct instances may serve the same entity by design.
        codes = error_codes(
            """
app two_tasks
  stack nextjs
  freedom guided
  entity Timesheet
    id: Id
    work_date: Date
  pattern ai_task FirstTask
    entity: Timesheet
  pattern ai_task SecondTask
    entity: Timesheet
"""
        )
        self.assertNotIn("E-CONFLICT", codes)

    def test_targetless_patterns_may_repeat(self):
        # notify has no entity target; two notifications with different
        # recipients are a legitimate use, not a conflict.
        codes = error_codes(
            """
app two_notifies
  stack nextjs
  freedom guided
  entity Item
    id: Id
    name: String
  pattern notify
    channel: email
    recipient: staff
  pattern notify
    channel: email
    recipient: manager
"""
        )
        self.assertNotIn("E-CONFLICT", codes)


class Discovery(unittest.TestCase):
    def top(self, query: str) -> str:
        results = find_patterns(query, REGISTRY)
        self.assertTrue(results, f"no patterns matched {query!r}")
        return results[0][1].name

    def test_keyword_query_still_finds_approval(self):
        self.assertEqual("approval", self.top("申請 承認"))

    def test_natural_japanese_sentence_finds_approval(self):
        self.assertEqual("approval", self.top("申請を承認したい"))

    def test_natural_japanese_sentence_finds_refund(self):
        self.assertEqual("refund", self.top("返金をしたい"))

    def test_english_stopwords_do_not_drown_the_match(self):
        self.assertEqual("refund", self.top("I want a refund flow"))


class FieldTypes(unittest.TestCase):
    MANIFEST = {
        "name": "typed",
        "version": "0.1.0",
        "intent": "test pattern with an enforced field type",
        "tags": ["typed"],
        "slots": {"entity": {"type": "entity", "required": True, "desc": "target"}},
        "field_types": {"work_date": "Date"},
        "artifacts": [{"path": "lib/typed.ts", "kind": "TS", "lines": 10, "desc": "x"}],
        "examples": [],
    }

    def registry(self):
        m = manifest_from_dict(self.MANIFEST)
        return {m.name: m}

    def app(self, field_type: str) -> str:
        return f"""
app typed_test
  stack nextjs
  freedom guided
  entity Shift
    id: Id
    work_date: {field_type}
  pattern typed Shift
    entity: Shift
"""

    def test_field_type_mismatch_is_an_error(self):
        self.assertIn("E-TYPE", error_codes(self.app("String"), self.registry()))

    def test_field_type_match_is_clean(self):
        self.assertNotIn("E-TYPE", error_codes(self.app("Date"), self.registry()))


if __name__ == "__main__":
    unittest.main()
