#!/usr/bin/env python3
"""Check Etsah files and print GitHub Actions annotations.

The composite action at the repository root runs this script. It uses the
Python standard library and ``etsah_api.check_file``, the same checker as
``etsah check``. Warnings are annotations and do not fail the run. Errors do.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional


DEFAULT_GLOB = "**/*.{etsah,prout}"
SCOPES = {"pull-request", "all"}
_BRACE = re.compile(r"\{([^{}]*)\}")
_SHA = re.compile(r"[0-9a-fA-F]{7,64}")
_SKIP_DIRS = {".git", "__pycache__", "node_modules"}


class ActionError(Exception):
    """A problem in the action itself, before or around the checker."""


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    code: str
    severity: str
    message: str

    def as_dict(self) -> dict[str, object]:
        return {
            "file": self.file,
            "line": self.line,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }


def expand_braces(pattern: str) -> list[str]:
    """Expand one ``{a,b}`` group. Patterns without a comma group stay as they are."""
    match = _BRACE.search(pattern)
    if match is None or "," not in match.group(1):
        return [pattern]
    options = [part.strip() for part in match.group(1).split(",") if part.strip()]
    if len(options) < 2:
        return [pattern]
    prefix = pattern[: match.start()]
    suffix = pattern[match.end() :]
    expanded: list[str] = []
    for option in options:
        expanded.extend(expand_braces(prefix + option + suffix))
    return expanded


def split_globs(value: str) -> list[str]:
    """Newline-separated globs. An empty value uses the default."""
    text = value.strip() or DEFAULT_GLOB
    patterns: list[str] = []
    for line in text.splitlines():
        piece = line.strip()
        if not piece or piece.startswith("#"):
            continue
        patterns.extend(expand_braces(piece))
    return patterns or expand_braces(DEFAULT_GLOB)


def split_hosts(value: str) -> Optional[list[str]]:
    """Comma- or whitespace-separated host names. Empty keeps the checker default."""
    parts = [part.strip() for part in re.split(r"[\s,]+", value.strip()) if part.strip()]
    return parts or None


def validate_pattern(pattern: str) -> None:
    if pattern.startswith("/") or pattern.startswith("\\"):
        raise ActionError(f"glob must be relative: {pattern}")
    if ".." in PurePosixPath(pattern.replace("\\", "/")).parts:
        raise ActionError(f"glob must stay in the workspace: {pattern}")


def matching_files(root: Path, patterns: list[str]) -> list[str]:
    """Workspace-relative paths that match any glob. Paths outside the root are dropped."""
    found: set[str] = set()
    root = root.resolve()
    for pattern in patterns:
        validate_pattern(pattern)
        for name in glob.glob(pattern, root_dir=root, recursive=True):
            relative = Path(name).as_posix().lstrip("./")
            parts = PurePosixPath(relative).parts
            if not relative or any(part in _SKIP_DIRS for part in parts):
                continue
            candidate = (root / relative).resolve()
            try:
                inside = candidate.relative_to(root)
            except ValueError:
                continue
            if not candidate.is_file():
                continue
            found.add(inside.as_posix())
    return sorted(found)


def select_files(matches: list[str], changed: set[str]) -> list[str]:
    """Keep matches that the pull request changes."""
    wanted = {item.replace("\\", "/").lstrip("./") for item in changed}
    return [item for item in matches if item in wanted]


def escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(value: str) -> str:
    return escape_data(value).replace(":", "%3A").replace(",", "%2C")


def annotation(finding: Finding) -> str:
    """One workflow command. ``line`` is omitted when the checker has no line."""
    kind = "warning" if finding.severity == "warning" else "error"
    props: list[str] = []
    if finding.file:
        props.append(f"file={escape_property(finding.file)}")
    if finding.line > 0:
        props.append(f"line={finding.line}")
    if finding.code:
        props.append(f"title={escape_property(finding.code)}")
    body = finding.message
    if finding.code:
        body = f"{finding.code}: {body}"
    prefix = f" {','.join(props)}" if props else ""
    return f"::{kind}{prefix}::{escape_data(body)}"


def git_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("GIT_"):
            env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def valid_sha(value: object) -> Optional[str]:
    if isinstance(value, str) and _SHA.fullmatch(value):
        return value
    return None


def git_diff_names(workspace: Path, base: str, head: str) -> Optional[set[str]]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--diff-filter=ACMRT", base, head],
        cwd=workspace,
        env=git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    names: set[str] = set()
    for raw in proc.stdout.split("\0"):
        if raw:
            names.add(raw.replace("\\", "/"))
    return names


def git_changed(workspace: Path, base: str, head: str) -> Optional[set[str]]:
    found = git_diff_names(workspace, base, head)
    if found is not None:
        return found
    for sha in (base, head):
        fetched = subprocess.run(
            ["git", "fetch", "--no-tags", "--depth=1", "origin", sha],
            cwd=workspace,
            env=git_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        if fetched.returncode != 0:
            return None
    return git_diff_names(workspace, base, head)


def next_link(header: str, api_root: str) -> Optional[str]:
    """Return the ``rel=next`` URL when it stays on the GitHub API origin."""
    for part in header.split(","):
        if 'rel="next"' not in part and "rel=next" not in part:
            continue
        match = re.search(r"<([^>]+)>", part)
        if match is None:
            continue
        url = match.group(1)
        if url.startswith(api_root + "/"):
            return url
    return None


def api_changed(number: int) -> Optional[set[str]]:
    """List pull-request files with the standard library. Missing credentials return None."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) is None:
        return None
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    if not api.startswith("https://"):
        return None
    url: Optional[str] = f"{api}/repos/{repo}/pulls/{number}/files?per_page=100"
    found: set[str] = set()
    seen: set[str] = set()
    pages = 0
    while url:
        if url in seen or pages >= 20:
            return None
        seen.add(url)
        pages += 1
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "etsah-check",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                link = response.headers.get("Link", "")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
            return None
        if not isinstance(payload, list):
            return None
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
                return None
            if item.get("status") == "removed":
                continue
            found.add(item["filename"].replace("\\", "/"))
        url = next_link(link, api)
    return found


def event_payload() -> dict:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        raise ActionError("GITHUB_EVENT_PATH is not set")
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        raise ActionError(f"could not read the event payload: {ex}") from ex
    if not isinstance(payload, dict):
        raise ActionError("event payload is not an object")
    return payload


def changed_in_pull_request(workspace: Path) -> Optional[set[str]]:
    """Paths changed by the pull request.

    ``None`` means this event is not a pull request, so the caller checks
    every match. A pull request whose file list cannot be read raises
    ``ActionError`` instead of checking the whole tree.
    """
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return None
    if not (workspace / ".git").exists():
        raise ActionError("the workspace has no checkout; add actions/checkout before this action")
    payload = event_payload()
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        raise ActionError("pull_request event has no pull request")
    base_obj = pull_request.get("base")
    head_obj = pull_request.get("head")
    base = valid_sha(base_obj.get("sha") if isinstance(base_obj, dict) else None)
    head = valid_sha(head_obj.get("sha") if isinstance(head_obj, dict) else None)
    if not base or not head:
        raise ActionError("pull request is missing a base or head sha")
    names = git_changed(workspace, base, head)
    number = pull_request.get("number")
    if names is None and isinstance(number, int) and number > 0:
        names = api_changed(number)
    if names is None:
        raise ActionError("could not list files changed in the pull request")
    return names


def action_root() -> Path:
    env = os.environ.get("GITHUB_ACTION_PATH")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


def workspace_root() -> Path:
    env = os.environ.get("GITHUB_WORKSPACE")
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


def check_files(
    action: Path,
    workspace: Path,
    files: list[str],
    allow_net: Optional[list[str]],
    deny_net: Optional[list[str]],
) -> list[Finding]:
    """Run the existing checker. This does not change ``etsah.py``."""
    root = str(action)
    if root not in sys.path:
        sys.path.insert(0, root)
    from etsah_api import check_file

    findings: list[Finding] = []
    for rel in files:
        try:
            result = check_file(workspace / rel, allow_net=allow_net, deny_net=deny_net)
        except OSError as ex:
            findings.append(Finding(rel, 0, "E-ACTION", "error", str(ex)))
            continue
        for item in result.diagnostics:
            message = item.message
            if item.fix:
                message = f"{message}\nfix: {item.fix}"
            findings.append(Finding(rel, item.line, item.code, item.severity, message))
    return findings


def write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        if "\n" in value or "\r" in value:
            handle.write(f"{name}<<ETSAH_END\n{value}\nETSAH_END\n")
        else:
            handle.write(f"{name}={value}\n")


def write_result(payload: dict[str, object]) -> None:
    explicit = os.environ.get("ETSAH_RESULT_PATH")
    if explicit:
        path = Path(explicit)
    else:
        temp = os.environ.get("RUNNER_TEMP")
        if not temp:
            return
        path = Path(temp) / "etsah-check.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def emit(findings: list[Finding], notice: str, checked: int) -> None:
    for finding in findings:
        print(annotation(finding))
    if notice:
        print(f"::notice::{escape_data(notice)}")
    errors = sum(1 for item in findings if item.severity != "warning")
    warnings = sum(1 for item in findings if item.severity == "warning")
    print(f"Checked {checked} file(s), {errors} error(s), {warnings} warning(s).")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"Checked {checked} file(s): {errors} error(s), {warnings} warning(s).\n")


def main() -> int:
    workspace = workspace_root()
    action = action_root()
    glob_text = os.environ.get("ETSAH_GLOB", "")
    scope = os.environ.get("ETSAH_SCOPE", "pull-request").strip() or "pull-request"
    allow_net = split_hosts(os.environ.get("ETSAH_ALLOW_NET", ""))
    deny_net = split_hosts(os.environ.get("ETSAH_DENY_NET", ""))
    files: list[str] = []
    notice = ""
    try:
        if scope not in SCOPES:
            raise ActionError("scope must be pull-request or all")
        patterns = split_globs(glob_text)
        matches = matching_files(workspace, patterns)
        if scope == "all":
            files = matches
            if not files:
                raise ActionError("no file matched the glob")
        else:
            changed = changed_in_pull_request(workspace)
            if changed is None:
                files = matches
                notice = "No pull request diff; checked every file that matches the glob."
                if not files:
                    notice = "No file matched the glob."
            else:
                files = select_files(matches, changed)
                if not files:
                    notice = "No changed file matches the glob."
        findings = check_files(action, workspace, files, allow_net, deny_net)
    except ActionError as ex:
        findings = [Finding("", 0, "E-ACTION", "error", str(ex))]
        files = []
    except Exception as ex:
        traceback.print_exc()
        findings = [Finding("", 0, "E-ACTION", "error", f"{type(ex).__name__}: {ex}")]
        files = []

    errors = [item for item in findings if item.severity != "warning"]
    warnings = [item for item in findings if item.severity == "warning"]
    payload: dict[str, object] = {
        "glob": glob_text.strip() or DEFAULT_GLOB,
        "scope": scope,
        "files": files,
        "errors": len(errors),
        "warnings": len(warnings),
        "diagnostics": [item.as_dict() for item in findings],
    }
    emit(findings, notice, len(files))
    write_result(payload)
    write_output("errors", str(len(errors)))
    write_output("warnings", str(len(warnings)))
    write_output("checked", ",".join(files))
    write_output("error-codes", ",".join(sorted({item.code for item in errors})))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
