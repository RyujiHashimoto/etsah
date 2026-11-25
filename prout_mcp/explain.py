"""Short explanations for PROUT diagnostic codes.

The text is the suggestion a person or an agent can apply. It does not change
the checker. Unknown codes stay unknown instead of being guessed.
"""
from __future__ import annotations

from typing import Any


# explanation, suggested fix, severity
_CODES: dict[str, tuple[str, str, str]] = {
    "E-PARSE": (
        "The source is not a PROUT program. The checker stops at the first syntax error.",
        "Correct the reported line. Indent with 2 spaces, and quote strings with double quotes.",
        "error",
    ),
    "E-DUP": (
        "Two declarations share a name, or the same effect is listed twice.",
        "Keep one of the declarations and rename or remove the other.",
        "error",
    ),
    "E-TYPE": (
        "A type, operator, argument, or return value does not match the declaration.",
        "Use the type the declaration expects, or change the declaration to the type you mean.",
        "error",
    ),
    "E-UNDEF": (
        "A variable, operation, or agent is used but not declared.",
        "Declare the name, or correct the spelling so it matches an existing declaration.",
        "error",
    ),
    "E-FIELD": (
        "The type has no field with that name. Idempotency keys use the same rule.",
        "Add the field to the type, or name a field that already exists.",
        "error",
    ),
    "E-ARITY": (
        "A call passes a different number of arguments than the operation declares.",
        "Pass one argument for each parameter.",
        "error",
    ),
    "E-EFF": (
        "The declared effects are not the effects of the operations the body calls, or an effect kind is unknown.",
        "Set eff to the union of the called operations' effects. When the diagnostic includes a fix line, that line is the union.",
        "error",
    ),
    "E-FLOW": (
        "A flow or task does not end in a return, or a statement comes after the return.",
        "End the body with one return of the declared type, and put every other statement before it.",
        "error",
    ),
    "E-PRE": (
        "A precondition is false at a call site the checker could evaluate.",
        "Make the condition true, or add a guard so the call is not reached when it is false.",
        "error",
    ),
    "W-PRE": (
        "The checker could not prove a precondition. The call still needs a runtime guard.",
        "Keep the precondition and enforce it when the call runs. This warning does not fail the check.",
        "warning",
    ),
    "E-POLICY": (
        "An agent, task, gate, network target, budget, or rate limit breaks a contract rule. "
        "External emit and net, every spend, and a contract write need a gate the task names. "
        "A network target must be on the allow list. The default allow list is stripe.",
        "Name a gate whose before list includes the effect, add the effect to the agent allow list, "
        "or pass the host in allow_net. Read the diagnostic message for which rule failed. "
        "Spend budgets are positive integer yen, and the per-run amount is at most the period amount.",
        "error",
    ),
    "E-SLOT": (
        "A pattern slot is missing or its value is not one the pattern accepts.",
        "Fill the required slot with an accepted value. proutc.py find lists the pattern and its slots.",
        "error",
    ),
    "E-PATTERN": (
        "The pattern name is not in the registry.",
        "Run proutc.py find and use a registered pattern name.",
        "error",
    ),
    "E-TARGET": (
        "The pattern has no entity, or the entity it names is not declared.",
        "Declare the entity and name it on the pattern.",
        "error",
    ),
    "E-ENTITY": (
        "An entity has no fields, or it is missing a field a pattern or idempotency key needs.",
        "Add the field named in the diagnostic.",
        "error",
    ),
    "E-CONFLICT": (
        "A pattern is applied more than once where only one application is allowed.",
        "Keep a single application of that pattern on the target.",
        "error",
    ),
    "E-CUSTOM": (
        "A custom block is missing a positive lines estimate or a why, or it is not allowed in this mode.",
        "Add a positive lines estimate. In guided mode, also add a why.",
        "error",
    ),
    "E-FREEDOM": (
        "freedom is not strict, guided, or open, or strict mode contains a custom block.",
        "Set freedom to strict, guided, or open. Move custom blocks out of strict mode.",
        "error",
    ),
    "E-STACK": (
        "The specification declares no stack target.",
        "Declare at least one stack target.",
        "error",
    ),
    "E-SCENE": (
        "A scene is missing its id, or a choice points at a scene that is not declared.",
        "Give every scene a unique id, and point each choice at one of those ids.",
        "error",
    ),
    "E-HOOK": (
        "A hook points at a missing target, an ambiguous target, or a target that was not applied.",
        "Apply the target pattern once, or point the hook at one custom block.",
        "error",
    ),
    "E-HOOK-ARG": (
        "A hook argument does not match the target slot or the roles declared by auth.",
        "Use a channel and recipient that the target pattern and auth.roles both allow.",
        "error",
    ),
    "W-SLOT": (
        "A slot name is not part of the pattern, so it was ignored.",
        "Remove the slot, or add it to the pattern definition if the pattern should accept it.",
        "warning",
    ),
    "W-POLICY": (
        "A pattern policy warning. An exempt slot was set. The message is not a legal opinion.",
        "Read the diagnostic and decide whether the exempt slot is what you meant.",
        "warning",
    ),
}


def explain_code(code: str) -> dict[str, Any]:
    """Return an explanation for ``code``. Matching ignores surrounding space and case."""
    raw = code.strip()
    found = _CODES.get(raw) or _CODES.get(raw.upper())
    if found is None:
        return {
            "code": raw,
            "known": False,
            "severity": "",
            "explanation": f"No explanation is registered for {raw}.",
            "suggested_fix": "Use the diagnostic message. Contract codes come from prout.py. Pattern codes come from proutc.py.",
        }
    explanation, suggested, severity = found
    canonical = raw if raw in _CODES else raw.upper()
    return {
        "code": canonical,
        "known": True,
        "severity": severity,
        "explanation": explanation,
        "suggested_fix": suggested,
    }
