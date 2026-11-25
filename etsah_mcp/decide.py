"""ALLOW, BLOCK, and ASK decisions over an Etsah contract.

Task verdicts follow the checker's diagnostics. A task with an error, or any
error that is not tied to one task, is BLOCK. A task that names a gate and
has no error is ASK. Anything else that passed is ALLOW.

Call verdicts are per operation, which is what a guard needs before it
forwards one ``tools/call``. A contract that still has an error is fail-closed:
every call is BLOCK. On a clean contract, an effect that needs a gate is ASK
only when a task names a gate whose ``before`` list includes that effect. The
same effect with no named gate is BLOCK. Network targets use the checker's
allow list, whose default is ``stripe``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from etsah import Parser, ProutError, format_effect
from etsah_api import check_source


ALLOW = "ALLOW"
BLOCK = "BLOCK"
ASK = "ASK"


@dataclass
class StepDecision:
    tool: str
    verdict: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "verdict": self.verdict, "reason": self.reason}


@dataclass
class LoadedContract:
    ok: bool
    diagnostics: tuple[Any, ...]
    ast_hash: str
    audit_id: str
    plans: tuple[dict[str, Any], ...]
    program: Any = None
    allow_net: set[str] = field(default_factory=set)
    deny_net: set[str] = field(default_factory=set)
    external: set[tuple[str, str]] = field(default_factory=set)
    contract_writes: set[tuple[str, str]] = field(default_factory=set)
    parse_error: str = ""


def load_contract(
    source: str,
    *,
    allow_net: Optional[list[str]] = None,
    deny_net: Optional[list[str]] = None,
) -> LoadedContract:
    """Parse and check ``source``. A syntax error is a contract with no plans."""
    checked = check_source(source, allow_net=allow_net, deny_net=deny_net)
    try:
        program = Parser(source).parse()
    except ProutError as ex:
        return LoadedContract(
            ok=False,
            diagnostics=checked.diagnostics,
            ast_hash="",
            audit_id="",
            plans=(),
            allow_net=set(allow_net) if allow_net else {"stripe"},
            deny_net=set(deny_net or []),
            parse_error=ex.message,
        )
    external: set[tuple[str, str]] = set()
    contract_writes: set[tuple[str, str]] = set()
    for policy in program.policies:
        external |= set(policy.external)
        contract_writes |= set(policy.contract)
    plans = tuple(
        _task_verdict(plan, checked, program) for plan in checked.plans
    )
    return LoadedContract(
        ok=checked.ok,
        diagnostics=checked.diagnostics,
        ast_hash=checked.ast_hash,
        audit_id=checked.audit_id,
        plans=plans,
        program=program,
        allow_net=set(allow_net) if allow_net else {"stripe"},
        deny_net=set(deny_net or []),
        external=external,
        contract_writes=contract_writes,
    )


def check_report(
    source: str,
    *,
    allow_net: Optional[list[str]] = None,
    deny_net: Optional[list[str]] = None,
) -> dict[str, Any]:
    """The ``etsah_check`` payload, including a verdict on every task."""
    loaded = load_contract(source, allow_net=allow_net, deny_net=deny_net)
    return {
        "ok": loaded.ok,
        "ast_hash": loaded.ast_hash,
        "audit_id": loaded.audit_id,
        "diagnostics": [_diagnostic(item) for item in loaded.diagnostics],
        "plans": list(loaded.plans),
    }


def check_plan(
    source: str,
    steps: list[Any],
    *,
    agent: str = "",
    allow_net: Optional[list[str]] = None,
    deny_net: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Verdict for each proposed tool call. ``ok`` is true only when every step is ALLOW."""
    loaded = load_contract(source, allow_net=allow_net, deny_net=deny_net)
    decisions = [decide_step(loaded, step, agent=agent) for step in steps]
    return {
        "ok": all(item.verdict == ALLOW for item in decisions),
        "needs_approval": any(item.verdict == ASK for item in decisions),
        "ast_hash": loaded.ast_hash,
        "audit_id": loaded.audit_id,
        "agent": agent,
        "steps": [item.as_dict() for item in decisions],
    }


def decide_call(loaded: LoadedContract, tool: str, *, agent: str = "") -> StepDecision:
    """Decide one tool name against an already loaded contract."""
    return _decide_tool(loaded, tool, agent=agent)


def gates_for_tool(loaded: LoadedContract, tool: str) -> list[tuple[str, str]]:
    """Named gates that cover every gated effect of ``tool``.

    Each item is ``(gate name, approver)``. A gate that no task names is
    omitted. An op that does not need a gate returns an empty list. This is
    the set a signed approval may name. It does not change the verdict.
    """
    if loaded.parse_error or not loaded.ok or loaded.program is None or not tool:
        return []
    ops = {op.name: op for op in loaded.program.ops}
    op = ops.get(tool)
    if op is None:
        return []
    gated = [effect for effect in op.effects if _needs_gate(effect, loaded)]
    if not gated:
        return []
    found: list[tuple[str, str]] = []
    for gate in _named_gates(loaded.program):
        covered = set(gate.before)
        if all(effect in covered for effect in gated):
            found.append((gate.name, gate.approver))
    return found


def decide_step(loaded: LoadedContract, step: Any, *, agent: str = "") -> StepDecision:
    if not isinstance(step, dict):
        return StepDecision(tool="", verdict=BLOCK, reason="a plan step must be an object with a tool name")
    tool = step.get("tool", step.get("name", ""))
    if not isinstance(tool, str):
        tool = ""
    return _decide_tool(loaded, tool.strip(), agent=agent)


def _decide_tool(loaded: LoadedContract, tool: str, *, agent: str) -> StepDecision:
    if loaded.parse_error:
        return StepDecision(tool=tool, verdict=BLOCK, reason=loaded.parse_error)
    if not loaded.ok:
        messages = [item.message for item in loaded.diagnostics if item.severity == "error"]
        summary = "; ".join(messages) if messages else "the contract check failed"
        return StepDecision(tool=tool, verdict=BLOCK, reason=f"the contract check failed: {summary}")
    if not tool:
        return StepDecision(tool=tool, verdict=BLOCK, reason="the step has no tool name")
    program = loaded.program
    ops = {op.name: op for op in program.ops}
    op = ops.get(tool)
    if op is None:
        return StepDecision(
            tool=tool,
            verdict=BLOCK,
            reason=f"tool {tool!r} is not declared as an op in the contract",
        )
    net_reasons: list[str] = []
    for _kind, target in (effect for effect in op.effects if effect[0] == "net"):
        if target in loaded.deny_net:
            net_reasons.append(f"network target {target!r} is denied by policy")
        elif target not in loaded.allow_net:
            net_reasons.append(f"network target {target!r} is not allowed by policy")
    if net_reasons:
        return StepDecision(tool=tool, verdict=BLOCK, reason="; ".join(net_reasons))
    gated = [effect for effect in op.effects if _needs_gate(effect, loaded)]
    named = _named_gates(program)
    uncovered = [effect for effect in gated if not any(effect in set(gate.before) for gate in named)]
    if uncovered:
        return StepDecision(
            tool=tool,
            verdict=BLOCK,
            reason="; ".join(_gap_message(effect, loaded.external) for effect in uncovered),
        )
    chosen, agent_error = _resolve_agent(program, tool, agent)
    if agent_error:
        return StepDecision(tool=tool, verdict=BLOCK, reason=agent_error)
    if chosen is not None:
        outside = [effect for effect in op.effects if effect not in set(chosen.allow)]
        if outside:
            listed = ", ".join(format_effect(effect) for effect in outside)
            return StepDecision(
                tool=tool,
                verdict=BLOCK,
                reason=f"effect {listed} is outside agent {chosen.name} allow",
            )
    if gated:
        approvers: list[str] = []
        for effect in gated:
            for gate in named:
                if effect not in set(gate.before):
                    continue
                approver = gate.approver or "?"
                approvers.append(f"{gate.name} (approver {approver}, before {format_effect(effect)})")
        return StepDecision(
            tool=tool,
            verdict=ASK,
            reason=f"human approval is required before {tool} runs. " + "; ".join(approvers),
        )
    if not op.effects:
        reason = f"op {tool} declares no effects"
    elif chosen is None:
        reason = f"op {tool} declares no effect that requires a gate"
    else:
        listed = ", ".join(format_effect(effect) for effect in op.effects)
        reason = f"{listed} is inside agent {chosen.name} allow and does not require a gate"
    return StepDecision(tool=tool, verdict=ALLOW, reason=reason)


def _task_verdict(plan: Any, checked: Any, program: Any) -> dict[str, Any]:
    payload = plan.as_dict()
    errors = [item for item in checked.diagnostics if item.severity == "error"]
    names = [item.name for item in checked.plans]
    own = [item for item in errors if _mentions_task(item.message, plan.name)]
    global_errors = [
        item for item in errors if not any(_mentions_task(item.message, name) for name in names)
    ]
    if own or global_errors:
        messages = [item.message for item in own + global_errors]
        payload["verdict"] = BLOCK
        payload["reason"] = "; ".join(messages)
        return payload
    if plan.gates:
        gates = {gate.name: gate for gate in program.gates}
        shown = []
        for name in plan.gates:
            gate = gates.get(name)
            if gate is not None and gate.approver:
                shown.append(f"{name} (approver {gate.approver})")
            else:
                shown.append(name)
        payload["verdict"] = ASK
        payload["reason"] = (
            "Human approval is required before the gated effects run. Gates: " + ", ".join(shown)
        )
        return payload
    payload["verdict"] = ALLOW
    payload["reason"] = "The task has no gated effect and the contract check reported no error for it."
    return payload


def _mentions_task(message: str, name: str) -> bool:
    return re.search(rf"task {re.escape(name)}(?!\w)", message) is not None


def _needs_gate(effect: tuple[str, str], loaded: LoadedContract) -> bool:
    kind, _target = effect
    if kind == "spend":
        return True
    if kind in {"emit", "net"} and effect in loaded.external:
        return True
    if kind == "write" and effect in loaded.contract_writes:
        return True
    return False


def _gap_message(effect: tuple[str, str], external: set[tuple[str, str]]) -> str:
    kind, _target = effect
    shown = format_effect(effect)
    if kind == "emit" and effect in external:
        return f"emit to an external party has no approval gate ({shown})"
    if kind == "net" and effect in external:
        return f"net to an external party has no approval gate ({shown})"
    if kind == "spend":
        return f"spend has no approval gate ({shown})"
    return f"a contract change has no approval gate ({shown})"


def _named_gates(program: Any) -> list[Any]:
    named = {name for task in program.tasks for name in task.gates}
    return [gate for gate in program.gates if gate.name in named]


def _resolve_agent(program: Any, tool: str, requested: str) -> tuple[Any, str]:
    agents = {agent.name: agent for agent in program.agents}
    if requested:
        chosen = agents.get(requested)
        if chosen is None:
            return None, f"unknown agent {requested!r}"
        return chosen, ""
    if not agents:
        return None, ""
    if len(agents) == 1:
        return next(iter(agents.values())), ""
    owners = []
    for task in program.tasks:
        if tool in _calls(task) and task.agent in agents and task.agent not in owners:
            owners.append(task.agent)
    if len(owners) == 1:
        return agents[owners[0]], ""
    return None, "the contract declares more than one agent; pass agent so the call is checked against one allow list"


def _calls(task: Any) -> set[str]:
    found: set[str] = set()
    for stmt in task.body:
        _walk(stmt.expr, found)
    return found


def _walk(expr: Any, found: set[str]) -> None:
    if expr is None:
        return
    if getattr(expr, "kind", "") == "call" and expr.name:
        found.add(expr.name)
    _walk(getattr(expr, "left", None), found)
    _walk(getattr(expr, "right", None), found)
    _walk(getattr(expr, "base", None), found)
    for arg in getattr(expr, "args", []) or []:
        _walk(arg, found)


def _diagnostic(item: Any) -> dict[str, Any]:
    return {
        "code": item.code,
        "line": item.line,
        "message": item.message,
        "severity": item.severity,
    }
