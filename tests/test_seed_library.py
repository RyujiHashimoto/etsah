"""Maple Shelf lend-and-record example: effect completeness."""
from __future__ import annotations

import subprocess
import sys
import unittest

from support import ROOT


class TestSeedLibrary(unittest.TestCase):
    def test_passing_file_exits_0(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "etsah.py"), "check", str(ROOT / "examples" / "seed_library.etsah")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK — no issues", proc.stdout)

    def test_broken_file_exits_1_missing_write(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "etsah.py"), "check", str(ROOT / "examples" / "seed_library_broken.etsah")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        output = proc.stdout + proc.stderr
        self.assertIn("E-EFF", output)
        self.assertIn("missing write(loans)", output)


if __name__ == "__main__":
    unittest.main()
