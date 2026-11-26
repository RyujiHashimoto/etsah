"""The getting-started sections name files and commands that still exist.

The English quickstart and the Japanese 「動かす」 section are the install
path. This checks the example files, relative links, and the install command
those sections tell a reader to run.
"""
from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ETSAH_PATH = re.compile(r"(?<![\w./-])((?:examples|patterns)/[\w./-]+\.etsah)")
REL_LINK = re.compile(r"\[[^\]]+\]\(([^)\s]+)")
LEGACY_COMMAND = re.compile(r"(?<![\w/])prout(?:c|-mcp)?(?![\w-])")

SECTIONS = (
    ("README.md", "## Quickstart", "\n## Check a pull request"),
    ("README.ja.md", "## 動かす", "\n## プルリクエストの検査"),
)


def _section(name: str, start: str, end: str) -> str:
    text = (ROOT / name).read_text(encoding="utf-8")
    start_at = text.index(start)
    end_at = text.index(end, start_at + len(start))
    return text[start_at:end_at]


class ReadmeQuickstartTests(unittest.TestCase):
    def test_quickstart_example_files_and_links_exist(self) -> None:
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        for name, start, end in SECTIONS:
            body = _section(name, start, end)
            paths = ETSAH_PATH.findall(body)
            self.assertTrue(paths, name)
            for rel in paths:
                self.assertTrue((ROOT / rel).is_file(), f"{name} missing {rel}")
            for target in REL_LINK.findall(body):
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                path = target.split("#", 1)[0]
                if not path:
                    continue
                self.assertTrue((ROOT / path).exists(), f"{name} link {target}")
            self.assertIn(".venv/bin/python -m pip install .", body)
            self.assertNotIn("pip install etsah", body)
            self.assertIn(f"prout-lang-{version}", body)
            self.assertIn("examples/refund_fixed.etsah", body)
            self.assertIn("examples/agents/support_reply.etsah", body)
            self.assertIn("check --json", body)
            self.assertIn("unittest discover -s tests", body)
            self.assertIn("etsahc", body)
            self.assertIn("etsah-mcp", body)
            self.assertNotIn("python3 etsah.py", body)
            self.assertNotIn("python3 patterns/etsahc.py", body)
            self.assertIsNone(LEGACY_COMMAND.search(body), name)
