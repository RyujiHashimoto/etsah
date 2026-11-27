#!/usr/bin/env python3
"""The composite action selects files and turns checker diagnostics into annotations."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
GOOD = (ROOT / "examples" / "ai_tasks.etsah").read_text(encoding="utf-8")
BAD = (ROOT / "examples" / "ai_tasks_broken.etsah").read_text(encoding="utf-8")

_SPEC = importlib.util.spec_from_file_location("etsah_action", ROOT / "scripts" / "etsah_action.py")
assert _SPEC is not None and _SPEC.loader is not None
etsah_action = importlib.util.module_from_spec(_SPEC)
sys.modules["etsah_action"] = etsah_action
_SPEC.loader.exec_module(etsah_action)


def run_action(
    workspace: Path,
    *,
    glob: str | None = None,
    scope: str = "all",
    extra: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    side = Path(tempfile.mkdtemp(prefix="etsah-action-"))
    output = side / "github-output.txt"
    result = side / "etsah-check.json"
    env = os.environ.copy()
    for key in (
        "GITHUB_EVENT_NAME",
        "GITHUB_EVENT_PATH",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_OUTPUT",
        "GITHUB_STEP_SUMMARY",
        "GITHUB_REPOSITORY",
        "ETSAH_GLOB",
        "ETSAH_SCOPE",
        "ETSAH_ALLOW_NET",
        "ETSAH_DENY_NET",
        "ETSAH_RESULT_PATH",
    ):
        env.pop(key, None)
    env["GITHUB_WORKSPACE"] = str(workspace)
    env["GITHUB_ACTION_PATH"] = str(ROOT)
    env["ETSAH_SCOPE"] = scope
    env["GITHUB_OUTPUT"] = str(output)
    env["ETSAH_RESULT_PATH"] = str(result)
    if glob is not None:
        env["ETSAH_GLOB"] = glob
    if extra:
        env.update(extra)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "etsah_action.py")],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc, output, result


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        env=etsah_action.git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return proc.stdout.strip()


def commit(root: Path, message: str) -> str:
    git(root, "add", ".")
    git(root, "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-m", message)
    return git(root, "rev-parse", "HEAD")


class AnnotationTests(unittest.TestCase):
    def test_error_command_escapes_file_and_message(self) -> None:
        line = etsah_action.annotation(
            etsah_action.Finding("a,b:c.etsah", 3, "E-POLICY", "error", "100%\nnext")
        )
        self.assertTrue(line.startswith("::error "))
        self.assertIn("file=a%2Cb%3Ac.etsah", line)
        self.assertIn("line=3", line)
        self.assertIn("title=E-POLICY", line)
        self.assertIn("E-POLICY: 100%25%0Anext", line)
        self.assertNotIn("\n", line)

    def test_warning_without_a_line_omits_line(self) -> None:
        line = etsah_action.annotation(etsah_action.Finding("a.etsah", 0, "W-PRE", "warning", "guard"))
        self.assertTrue(line.startswith("::warning "))
        self.assertNotIn("line=", line)
        self.assertIn("title=W-PRE", line)

    def test_braces_expand_once_per_group(self) -> None:
        self.assertEqual(
            etsah_action.split_globs("**/*.{etsah,prout}"),
            ["**/*.etsah", "**/*.prout"],
        )
        self.assertEqual(etsah_action.split_globs(""), etsah_action.split_globs(etsah_action.DEFAULT_GLOB))

    def test_hosts_split_on_commas_and_whitespace(self) -> None:
        self.assertEqual(etsah_action.split_hosts("github, stripe\nemail"), ["github", "stripe", "email"])
        self.assertIsNone(etsah_action.split_hosts("  "))

    def test_parent_and_absolute_globs_are_rejected(self) -> None:
        with self.assertRaises(etsah_action.ActionError):
            etsah_action.matching_files(ROOT, ["../secrets.etsah"])
        with self.assertRaises(etsah_action.ActionError):
            etsah_action.matching_files(ROOT, ["/tmp/*.etsah"])


class SelectionTests(unittest.TestCase):
    def test_default_glob_matches_both_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ok.etsah").write_text("a\n", encoding="utf-8")
            nested = root / "desk"
            nested.mkdir()
            (nested / "old.prout").write_text("b\n", encoding="utf-8")
            (nested / "note.txt").write_text("c\n", encoding="utf-8")
            git_dir = root / ".git"
            git_dir.mkdir()
            (git_dir / "hidden.etsah").write_text("d\n", encoding="utf-8")
            found = etsah_action.matching_files(root, etsah_action.split_globs(""))
        self.assertEqual(found, ["desk/old.prout", "ok.etsah"])

    def test_pull_request_intersection_keeps_changed_paths(self) -> None:
        matches = ["examples/ai_tasks.etsah", "examples/ai_tasks_broken.etsah"]
        chosen = etsah_action.select_files(matches, {"examples/ai_tasks.etsah", "README.md"})
        self.assertEqual(chosen, ["examples/ai_tasks.etsah"])

    def test_next_link_stays_on_the_api_origin(self) -> None:
        header = (
            '<https://api.github.com/repos/acme/demo/pulls/1/files?page=2>; rel="next", '
            '<https://example.invalid/steal>; rel="next"'
        )
        self.assertEqual(
            etsah_action.next_link(header, "https://api.github.com"),
            "https://api.github.com/repos/acme/demo/pulls/1/files?page=2",
        )
        self.assertIsNone(
            etsah_action.next_link('<https://example.invalid/steal>; rel="next"', "https://api.github.com")
        )

    def test_git_diff_lists_the_added_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init")
            (root / "ok.etsah").write_text("a\n", encoding="utf-8")
            base = commit(root, "base")
            (root / "new.etsah").write_text("b\n", encoding="utf-8")
            head = commit(root, "add")
            names = etsah_action.git_changed(root, base, head)
        self.assertEqual(names, {"new.etsah"})


class CheckerActionTests(unittest.TestCase):
    def test_allow_net_is_passed_to_the_checker(self) -> None:
        denied, _, _ = run_action(ROOT, glob="examples/agents/github_triage.etsah")
        self.assertEqual(denied.returncode, 1, denied.stdout)
        self.assertIn("E-POLICY", denied.stdout)
        allowed, _, result = run_action(
            ROOT,
            glob="examples/agents/github_triage.etsah",
            extra={"ETSAH_ALLOW_NET": "github"},
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr + allowed.stdout)
        data = json.loads(result.read_text(encoding="utf-8"))
        self.assertEqual(data["errors"], 0)
        self.assertEqual(data["files"], ["examples/agents/github_triage.etsah"])

    def test_valid_contract_exits_zero_without_an_error_annotation(self) -> None:
        proc, output, result = run_action(ROOT, glob="examples/ai_tasks.etsah")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("::error ", proc.stdout)
        data = json.loads(result.read_text(encoding="utf-8"))
        self.assertEqual(data["files"], ["examples/ai_tasks.etsah"])
        self.assertEqual(data["errors"], 0)
        text = output.read_text(encoding="utf-8")
        self.assertIn("errors=0", text)
        self.assertIn("checked=examples/ai_tasks.etsah", text)

    def test_warning_is_an_annotation_and_does_not_fail(self) -> None:
        proc, _, result = run_action(ROOT, glob="examples/refund_fixed.etsah")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("::warning ", proc.stdout)
        self.assertIn("title=W-PRE", proc.stdout)
        self.assertNotIn("::error ", proc.stdout)
        data = json.loads(result.read_text(encoding="utf-8"))
        self.assertEqual(data["errors"], 0)
        self.assertGreaterEqual(data["warnings"], 1)

    def test_broken_contract_fails_with_policy_annotation(self) -> None:
        proc, _, result = run_action(ROOT, glob="examples/ai_tasks_broken.etsah")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("::error ", proc.stdout)
        self.assertIn("file=examples/ai_tasks_broken.etsah", proc.stdout)
        self.assertIn("line=99", proc.stdout)
        self.assertIn("title=E-POLICY", proc.stdout)
        self.assertIn("E-POLICY:", proc.stdout)
        data = json.loads(result.read_text(encoding="utf-8"))
        codes = {item["code"] for item in data["diagnostics"] if item["severity"] == "error"}
        self.assertIn("E-POLICY", codes)
        self.assertGreaterEqual(data["errors"], 1)
        self.assertEqual(data["glob"], "examples/ai_tasks_broken.etsah")

    def test_legacy_extension_is_checked_by_the_default_glob(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "desk.prout").write_text(GOOD, encoding="utf-8")
            (root / "note.txt").write_text("not a contract\n", encoding="utf-8")
            proc, _, result = run_action(root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(result.read_text(encoding="utf-8"))
        self.assertEqual(data["files"], ["desk.prout"])
        self.assertEqual(data["errors"], 0)

    def test_scope_all_fails_when_nothing_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.txt").write_text("x\n", encoding="utf-8")
            proc, _, result = run_action(root, glob="**/*.etsah")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("::error ", proc.stdout)
        self.assertIn("E-ACTION", proc.stdout)
        data = json.loads(result.read_text(encoding="utf-8"))
        self.assertEqual(data["files"], [])
        self.assertEqual(data["errors"], 1)

    def test_pull_request_ignores_an_unchanged_broken_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init")
            (root / "kept.etsah").write_text(GOOD, encoding="utf-8")
            (root / "skipped.etsah").write_text(BAD, encoding="utf-8")
            base = commit(root, "base")
            (root / "kept.etsah").write_text(GOOD + "\n# touched\n", encoding="utf-8")
            head = commit(root, "edit the valid contract")
            event = root / "event.json"
            event.write_text(
                json.dumps(
                    {
                        "pull_request": {
                            "number": 1,
                            "base": {"sha": base},
                            "head": {"sha": head},
                        }
                    }
                ),
                encoding="utf-8",
            )
            proc, _, result = run_action(
                root,
                scope="pull-request",
                extra={
                    "GITHUB_EVENT_NAME": "pull_request",
                    "GITHUB_EVENT_PATH": str(event),
                },
            )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = json.loads(result.read_text(encoding="utf-8"))
        self.assertEqual(data["files"], ["kept.etsah"])
        self.assertEqual(data["errors"], 0)

    def test_pull_request_fails_when_the_broken_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init")
            (root / "kept.etsah").write_text(GOOD, encoding="utf-8")
            (root / "broken.etsah").write_text(BAD, encoding="utf-8")
            base = commit(root, "base")
            (root / "broken.etsah").write_text(BAD + "\n# touched\n", encoding="utf-8")
            head = commit(root, "edit the broken contract")
            event = root / "event.json"
            event.write_text(
                json.dumps({"pull_request": {"number": 7, "base": {"sha": base}, "head": {"sha": head}}}),
                encoding="utf-8",
            )
            proc, _, result = run_action(
                root,
                scope="pull-request",
                extra={
                    "GITHUB_EVENT_NAME": "pull_request",
                    "GITHUB_EVENT_PATH": str(event),
                },
            )
        self.assertEqual(proc.returncode, 1, proc.stdout)
        data = json.loads(result.read_text(encoding="utf-8"))
        self.assertEqual(data["files"], ["broken.etsah"])
        codes = {item["code"] for item in data["diagnostics"] if item["severity"] == "error"}
        self.assertIn("E-POLICY", codes)
        self.assertIn("file=broken.etsah", proc.stdout)

    def test_missing_checkout_on_a_pull_request_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ok.etsah").write_text(GOOD, encoding="utf-8")
            proc, _, _ = run_action(
                root,
                scope="pull-request",
                extra={"GITHUB_EVENT_NAME": "pull_request"},
            )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("actions/checkout", proc.stdout)


class ActionMetadataTests(unittest.TestCase):
    def test_action_yml_is_a_composite_with_the_same_default(self) -> None:
        text = (ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertIn(f'default: "{etsah_action.DEFAULT_GLOB}"', text)
        self.assertIn("using: composite", text)
        self.assertIn("actions/setup-python@v5", text)
        self.assertIn("Ryuji Hashimoto (J-PROUT Inc.)", text)
        uses = re.findall(r"uses:\s*(\S+)", text)
        self.assertEqual(uses, ["actions/setup-python@v5"])

    def test_ci_calls_the_local_action_on_a_passing_and_a_broken_file(self) -> None:
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("uses: ./", text)
        self.assertIn("examples/ai_tasks.etsah", text)
        self.assertIn("examples/ai_tasks_broken.etsah", text)
        self.assertIn("continue-on-error: true", text)

    def test_readmes_pin_main_and_say_tags_are_unpublished(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        japanese = (ROOT / "README.ja.md").read_text(encoding="utf-8")
        for text in (english, japanese):
            self.assertIn("RyujiHashimoto/etsah@main", text)
            self.assertIn("uses: ./", text)
        self.assertIn("Tags are not published", english)
        self.assertIn("タグは未公開", japanese)


if __name__ == "__main__":
    unittest.main()
