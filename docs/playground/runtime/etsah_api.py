"""Stable read-only API over the Etsah checker.

``check_source`` and ``check_file`` call the existing parser and checker.
They do not run tasks, send mail, or count budgets. Syntax errors become
one ``E-PARSE`` diagnostic; the checker still reports the first syntax
error only.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional, Union

from etsah import (
    Checker,
    Parser,
    ProutError,
    TaskDef,
    ast_digest,
    ast_hash,
    format_effect,
    task_plan,
)
from etsah import _format_trigger


PathLike = Union[str, Path]


@dataclass(frozen=True)
class Diagnostic:
    """One checker finding. ``line`` is 1-based and 0 when the checker has none."""

    code: str
    message: str
    line: int
    severity: str = "error"
    fix: str = ""

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "line": self.line,
            "severity": self.severity,
        }
        if self.fix:
            data["fix"] = self.fix
        return data


@dataclass(frozen=True)
class TaskPlan:
    """One task, in source order, plus the checker's rendered plan block."""

    name: str
    agent: str
    trigger: str
    effects: tuple[str, ...]
    gates: tuple[str, ...]
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "agent": self.agent,
            "trigger": self.trigger,
            "effects": list(self.effects),
            "gates": list(self.gates),
            "text": self.text,
        }


@dataclass(frozen=True)
class Result:
    """Outcome of a check.

    ``ok`` is true when there is no error-severity diagnostic. Warnings stay
    in ``diagnostics`` and still leave ``ok`` true. ``ast_hash`` is the
    12-character display prefix; ``audit_id`` is the full SHA-256. Both are
    empty when the source does not parse.
    """

    ok: bool
    diagnostics: tuple[Diagnostic, ...]
    ast_hash: str
    audit_id: str
    plans: tuple[TaskPlan, ...]
    path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "ast_hash": self.ast_hash,
            "audit_id": self.audit_id,
            "path": self.path,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "plans": [item.as_dict() for item in self.plans],
        }


def check_source(
    text: str,
    *,
    allow_net: Optional[Sequence[str]] = None,
    deny_net: Optional[Sequence[str]] = None,
) -> Result:
    """Parse and check Etsah source.

    ``allow_net`` replaces the checker's default host list (``stripe``) when
    it is non-empty. An omitted or empty list keeps that default. This is the
    checker's own rule.
    """
    try:
        program = Parser(text).parse()
    except ProutError as ex:
        diagnostic = Diagnostic(code="E-PARSE", message=ex.message, line=ex.line, severity="error")
        return Result(ok=False, diagnostics=(diagnostic,), ast_hash="", audit_id="", plans=())
    return _from_program(program, allow_net=allow_net, deny_net=deny_net)


def check_file(
    path: PathLike,
    *,
    allow_net: Optional[Sequence[str]] = None,
    deny_net: Optional[Sequence[str]] = None,
) -> Result:
    """Read ``path`` as UTF-8 and check it. Missing files raise ``OSError``."""
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    result = check_source(text, allow_net=allow_net, deny_net=deny_net)
    return replace(result, path=str(file_path))


def _from_program(
    program: Any,
    *,
    allow_net: Optional[Sequence[str]],
    deny_net: Optional[Sequence[str]],
) -> Result:
    checker = Checker(
        program,
        allow_net=list(allow_net) if allow_net is not None else None,
        deny_net=list(deny_net) if deny_net is not None else None,
    )
    found = checker.check()
    diagnostics = tuple(
        Diagnostic(
            code=item.code,
            message=item.message,
            line=item.line,
            severity=item.severity,
            fix=item.fix or "",
        )
        for item in found
    )
    blocks = _plan_blocks(task_plan(program))
    plans = tuple(
        _task_plan(task, blocks[index] if index < len(blocks) else "")
        for index, task in enumerate(program.tasks)
    )
    return Result(
        ok=not any(item.severity == "error" for item in diagnostics),
        diagnostics=diagnostics,
        ast_hash=ast_hash(program),
        audit_id=ast_digest(program),
        plans=plans,
    )


def _task_plan(task: TaskDef, text: str) -> TaskPlan:
    return TaskPlan(
        name=task.name,
        agent=task.agent,
        trigger=_format_trigger(task),
        effects=tuple(format_effect(effect) for effect in task.effects),
        gates=tuple(task.gates),
        text=text,
    )


def _plan_blocks(lines: list[str]) -> list[str]:
    """Split ``task_plan`` into one block per task, dropping the header and audit line."""
    blocks: list[list[str]] = []
    for line in lines:
        if line.startswith("    "):
            if blocks:
                blocks[-1].append(line)
            continue
        if line.startswith("  "):
            blocks.append([line])
    return ["\n".join(block) for block in blocks]


__all__ = [
    "Diagnostic",
    "Result",
    "TaskPlan",
    "check_file",
    "check_source",
]
