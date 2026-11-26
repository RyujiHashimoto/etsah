"""The getting-started sections name files and commands that still exist.

The English quickstart and the Japanese 「動かす」 section are the install
path. This checks the example files, relative links, and the install command
those sections tell a reader to run. It also requires the Windows translation
of the refund check. That translation is text only; CI does not run it.
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
            self.assertIn("PYTHONPATH=tests", body)
            self.assertIn("python3 -m unittest test_checker.TestCli", body)
            self.assertIn("etsahc", body)
            self.assertIn("etsah-mcp", body)
            self.assertNotIn("python3 etsah.py", body)
            self.assertNotIn("python3 patterns/etsahc.py", body)
            self.assertIsNone(LEGACY_COMMAND.search(body), name)
            self.assertIn("ubuntu-latest", body)
            self.assertIn(".venv\\Scripts\\python -m pip install .", body)
            self.assertRegex(
                body,
                r"(?m)^\.venv\\Scripts\\etsah check examples/refund_fixed\.etsah$",
            )
            self.assertRegex(
                body,
                r"(?m)^\.venv\\Scripts\\etsah check examples/agents/support_reply\.etsah$",
            )
            self.assertRegex(
                body,
                r"(?m)^\.venv\\Scripts\\etsah check --json examples/agents/support_reply\.etsah$",
            )
            self.assertIn(
                r".\.venv\Scripts\etsah check examples/refund_fixed.etsah",
                body,
            )
            self.assertIn(
                r".\.venv\Scripts\etsah check examples/agents/support_reply.etsah",
                body,
            )
            self.assertIn(
                r".\.venv\Scripts\etsah check --json examples/agents/support_reply.etsah",
                body,
            )
            if name == "README.md":
                self.assertIn("path translation of the Linux block", body)
                self.assertIn("not a recorded run", body)
            else:
                self.assertIn("パスだけ置き換えたもの", body)
                self.assertIn("実行結果の記録ではありません", body)

    def test_contributing_documents_one_test_class(self) -> None:
        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("python3 -m unittest discover -s tests", text)
        self.assertIn("PYTHONPATH=tests python3 -m unittest test_checker.TestCli", text)
        self.assertIn("set PYTHONPATH=tests", text)
        self.assertIn('$env:PYTHONPATH="tests"', text)
        self.assertIn("CI has not run them", text)
