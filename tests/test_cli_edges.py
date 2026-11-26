"""Pins neighboring CLI edges that TestCli does not cover.

`ast` on a declaration that does not start at column 0, and `check` on an
empty file or a comment-only file. These cases record the current behavior.
A mismatch means the test is wrong; do not change etsah.py to satisfy it.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT

EMPTY_HASH = "6bafdd5f044b"
AST_PARSE_STDERR = (
    "[ERROR] E-PARSE (line 1): top-level declarations must start at column 0\n"
)


class TestCliEdges(unittest.TestCase):
    def _run(self, source: str, *args: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.etsah"
            path.write_text(source, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(ROOT / "etsah.py"), *args, str(path)],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_ast_rejects_indented_declaration(self) -> None:
        proc = self._run("  type Broken\n", "ast")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, AST_PARSE_STDERR)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_empty_and_comment_only_share_hash(self) -> None:
        for source in ("", "# just a note\n"):
            with self.subTest(source=source):
                proc = self._run(source, "check")
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn("OK — no issues", proc.stdout)
                self.assertIn("0 error(s), 0 warning(s)", proc.stdout)
                self.assertIn(EMPTY_HASH, proc.stdout)
                self.assertEqual(proc.stderr, "")

    def test_check_json_empty_and_comment_only(self) -> None:
        for source in ("", "# just a note\n"):
            with self.subTest(source=source):
                proc = self._run(source, "check", "--json")
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(proc.stderr, "")
                payload = json.loads(proc.stdout)
                self.assertEqual(set(payload), {"diagnostics", "hash", "manifest"})
                self.assertEqual(payload["diagnostics"], [])
                self.assertEqual(payload["hash"], EMPTY_HASH)
                manifest = payload["manifest"]
                self.assertEqual(manifest["agents"], [])
                self.assertEqual(manifest["hash"], EMPTY_HASH)
                self.assertIsNone(manifest["signature"])
                self.assertNotIn("audit_id", payload)
                self.assertNotIn("plan", payload)
