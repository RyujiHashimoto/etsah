import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from support import PATTERNS
from proutc import cmd_build, DEFAULT_REGISTRY


class SceneBuildTests(unittest.TestCase):
    def test_examples_and_invalid_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            args = argparse.Namespace(out=out, registry=DEFAULT_REGISTRY)
            with contextlib.redirect_stdout(io.StringIO()):
                for name in ("moon_garden", "fraction_quiz"):
                    args.file = PATTERNS / "examples" / (name + ".prout")
                    self.assertEqual(cmd_build(args), 0)
                    self.assertIn('href="story.html"', (out / "report.html").read_text())
                    page = (out / "story.html").read_text()
                    self.assertEqual(page.count("<section "), 3)
                    self.assertIn('href="#scene-', page)
                source = args.file.read_text()
                args.file = out / "broken.prout"
                for broken in (
                    source.replace('"hint"', '"absent"'),
                    source.replace("scene hint", "scene answer"),
                    source.replace('[["1切れ", "hint"], ["2切れ", "answer"]]', '["bad"]'),
                ):
                    args.file.write_text(broken)
                    self.assertEqual(cmd_build(args), 1)
                    self.assertFalse((out / "story.html").exists())
                    self.assertIn("E-SCENE", (out / "manifest.json").read_text())
