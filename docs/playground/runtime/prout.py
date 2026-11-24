#!/usr/bin/env python3
"""
PROUT v0.1 — a small AI-native contract language prototype.

The language is line-oriented. Expressions use a Pratt parser.
The checker verifies types, effects, preconditions, and policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional, Union


# ---------------------------------------------------------------------------
# Errors and diagnostics
# ---------------------------------------------------------------------------

class ProutError(Exception):
    def __init__(self, message: str, line: int = 0):
        super().__init__(message)
        self.message = message
        self.line = line


@dataclass
class Diagnostic:
    code: str
    severity: str
    message: str
    line: int = 0
    fix: Optional[str] = None
    detail: Optional[str] = None

    def as_dict(self) -> dict:
        d = {
            "code": self.code,
            "severity": self.severity,
            "line": self.line,
            "message": self.message,
        }
        if self.fix:
            d["fix"] = self.fix
        if self.detail:
            d["detail"] = self.detail
        return d

    def render(self) -> str:
        loc = f"line {self.line}" if self.line else "no line"
        s = f"[{self.severity.upper()}] {self.code} ({loc}): {self.message}"
        if self.fix:
            s += f"\n    fix: {self.fix}"
        return s


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------

@dataclass
class Expr:
    kind: str
    line: int = 0
    name: str = ""
    value: Any = None
    args: list["Expr"] = field(default_factory=list)
    left: Optional["Expr"] = None
    right: Optional["Expr"] = None
    base: Optional["Expr"] = None


@dataclass
class Stmt:
    kind: str
    line: int = 0
    name: str = ""
    expr: Optional[Expr] = None


@dataclass
class TypeDef:
    name: str
    fields: dict[str, str] = field(default_factory=dict)
    opaque: bool = False
    line: int = 0


@dataclass
class OpDef:
    name: str
    params: list[tuple[str, str]]
    ret: str
    line: int = 0
    doc: str = ""
    effects: list[tuple[str, str]] = field(default_factory=list)
    pres: list[Expr] = field(default_factory=list)
    posts: list[Expr] = field(default_factory=list)
    impl: Optional[Expr] = None
    native: bool = False


@dataclass
class FlowDef:
    name: str
    params: list[tuple[str, str]]
    ret: str
    line: int = 0
    doc: str = ""
    summary: str = ""
    effects: list[tuple[str, str]] = field(default_factory=list)
    body: list[Stmt] = field(default_factory=list)


@dataclass
class AgentDef:
    """Capability envelope for one AI. Budgets are integer yen."""

    name: str
    line: int = 0
    doc: str = ""
    allow: list[tuple[str, str]] = field(default_factory=list)
    budget_run: int = 0
    budget_period: int = 0
    limit_net: int = 0
    limit_emit: int = 0
    audit: str = ""
    allow_declared: bool = False


@dataclass
class GateDef:
    """Human approval that must cover an external effect before it runs."""

    name: str
    line: int = 0
    doc: str = ""
    approver: str = ""
    before: list[tuple[str, str]] = field(default_factory=list)
    on_approved: str = ""


@dataclass
class PolicyDef:
    """Which effects are external, payments, or contract changes."""

    line: int = 0
    external: list[tuple[str, str]] = field(default_factory=list)
    payment: list[tuple[str, str]] = field(default_factory=list)
    contract: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class TaskDef:
    """One job inside an agent's envelope. The body calls existing ops."""

    name: str
    params: list[tuple[str, str]]
    ret: str
    line: int = 0
    doc: str = ""
    agent: str = ""
    trigger_kind: str = ""
    trigger_value: str = ""
    effects: list[tuple[str, str]] = field(default_factory=list)
    gates: list[str] = field(default_factory=list)
    key: str = ""
    retry: int = 0
    escalate: str = ""
    budget_run: Optional[int] = None
    budget_period: Optional[int] = None
    body: list[Stmt] = field(default_factory=list)
    agent_declared: bool = False
    trigger_declared: bool = False
    eff_declared: bool = False
    key_declared: bool = False
    retry_declared: bool = False


@dataclass
class Program:
    types: list[TypeDef] = field(default_factory=list)
    ops: list[OpDef] = field(default_factory=list)
    flows: list[FlowDef] = field(default_factory=list)
    source: str = ""
    agents: list[AgentDef] = field(default_factory=list)
    tasks: list[TaskDef] = field(default_factory=list)
    gates: list[GateDef] = field(default_factory=list)
    policies: list[PolicyDef] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Expression lexer and parser
# ---------------------------------------------------------------------------

# DATE and DECIMAL are tried before NUMBER so `2026-11-23` and `1800.50`
# are single tokens. A spaced `2026 - 11 - 23` stays subtraction: the
# hyphens are separated by whitespace, which DATE does not cross.
TOKEN_RE = re.compile(
    r"""
    (?P<WS>\s+)
  | (?P<DATE>\d{4}-\d{2}-\d{2})
  | (?P<DECIMAL>\d+\.\d+)
  | (?P<NUMBER>\d+)
  | (?P<STRING>"(?:\\.|[^"\\])*")
  | (?P<IDENT>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<OP><=|>=|==|!=|&&|\|\||->|[()\{\},:+\-*/<>!=])
""",
    re.VERBOSE,
)


def parse_iso_date(value: str) -> Optional[date]:
    """Zero-padded Gregorian date, or None when the calendar day does not exist."""
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    if parsed.isoformat() != value:
        return None
    return parsed


def tokenize(s: str, line: int) -> list[tuple[str, str, int]]:
    tokens: list[tuple[str, str, int]] = []
    pos = 0
    while pos < len(s):
        m = TOKEN_RE.match(s, pos)
        if not m:
            raise ProutError(f"unexpected character {s[pos]!r}", line)
        pos = m.end()
        if m.lastgroup == "WS":
            continue
        tokens.append((m.lastgroup or "", m.group(), line))
    tokens.append(("EOF", "", line))
    return tokens


class ExprParser:
    def __init__(self, tokens: list[tuple[str, str, int]]):
        self.tokens = tokens
        self.i = 0

    def peek(self) -> tuple[str, str, int]:
        return self.tokens[self.i]

    def advance(self) -> tuple[str, str, int]:
        t = self.tokens[self.i]
        self.i += 1
        return t

    def match(self, *ops: str) -> bool:
        if self.peek()[1] in ops:
            self.i += 1
            return True
        return False

    def expect(self, op: str) -> tuple[str, str, int]:
        t = self.peek()
        if t[1] != op:
            raise ProutError(f"expected {op!r}, got {t[1]!r}", t[2])
        self.i += 1
        return t

    def parse(self) -> Expr:
        e = self.parse_or()
        if self.peek()[0] != "EOF":
            raise ProutError(f"unexpected token {self.peek()[1]!r}", self.peek()[2])
        return e

    def parse_or(self) -> Expr:
        e = self.parse_and()
        while self.match("||"):
            op = self.tokens[self.i - 1]
            e = Expr("bin", line=op[2], name="||", left=e, right=self.parse_and())
        return e

    def parse_and(self) -> Expr:
        e = self.parse_cmp()
        while self.match("&&"):
            op = self.tokens[self.i - 1]
            e = Expr("bin", line=op[2], name="&&", left=e, right=self.parse_cmp())
        return e

    def parse_cmp(self) -> Expr:
        e = self.parse_add()
        if self.peek()[1] in ("==", "!=", "<", "<=", ">", ">="):
            op = self.advance()
            e = Expr("bin", line=op[2], name=op[1], left=e, right=self.parse_add())
        return e

    def parse_add(self) -> Expr:
        e = self.parse_mul()
        while self.peek()[1] in ("+", "-"):
            op = self.advance()
            e = Expr("bin", line=op[2], name=op[1], left=e, right=self.parse_mul())
        return e

    def parse_mul(self) -> Expr:
        e = self.parse_unary()
        while self.peek()[1] in ("*", "/"):
            op = self.advance()
            e = Expr("bin", line=op[2], name=op[1], left=e, right=self.parse_unary())
        return e

    def parse_unary(self) -> Expr:
        if self.peek()[1] == "!":
            op = self.advance()
            return Expr("unary", line=op[2], name="!", left=self.parse_unary())
        if self.peek()[1] == "-":
            op = self.advance()
            return Expr("bin", line=op[2], name="-", left=Expr("lit", line=op[2], name="int", value=0), right=self.parse_unary())
        return self.parse_primary()

    def parse_primary(self) -> Expr:
        kind, value, line = self.peek()
        if kind == "DATE":
            self.advance()
            parsed = parse_iso_date(value)
            if parsed is None:
                raise ProutError(f"invalid date {value}", line)
            return Expr("lit", line=line, name="date", value=parsed)
        if kind == "DECIMAL":
            self.advance()
            return Expr("lit", line=line, name="dec", value=Decimal(value))
        if kind == "NUMBER":
            self.advance()
            return Expr("lit", line=line, name="int", value=int(value))
        if kind == "STRING":
            self.advance()
            return Expr("lit", line=line, name="str", value=json.loads(value))
        if kind == "IDENT":
            self.advance()
            name = value
            if self.match("("):
                args: list[Expr] = []
                if not self.match(")"):
                    while True:
                        args.append(self.parse_or())
                        if self.match(")"):
                            break
                        self.expect(",")
                return Expr("call", line=line, name=name, args=args)
            if name in ("true", "false"):
                return Expr("lit", line=line, name="bool", value=(name == "true"))
            if "." in name:
                parts = name.split(".")
                e = Expr("var", line=line, name=parts[0])
                for p in parts[1:]:
                    e = Expr("field", line=line, base=e, name=p)
                return e
            return Expr("var", line=line, name=name)
        if value == "(":
            self.advance()
            e = self.parse_or()
            self.expect(")")
            return e
        raise ProutError(f"unexpected token {value!r}", line)


def parse_expr(s: str, line: int) -> Expr:
    return ExprParser(tokenize(s, line)).parse()


# ---------------------------------------------------------------------------
# Line-oriented program parser
# ---------------------------------------------------------------------------

@dataclass
class Line:
    indent: int
    text: str
    no: int


def strip_comment(s: str) -> str:
    out: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
                out.append(ch)
            elif ch == "#":
                break
            else:
                out.append(ch)
    return "".join(out).rstrip()


def preprocess(text: str) -> list[Line]:
    lines: list[Line] = []
    for no, raw in enumerate(text.splitlines(), 1):
        s = strip_comment(raw)
        if not s.strip():
            continue
        leading = s[: len(s) - len(s.lstrip(" \t"))]
        if "\t" in leading:
            raise ProutError("tabs are not allowed for indentation", no)
        indent = len(leading)
        if indent % 2 != 0:
            raise ProutError("indentation must be a multiple of 2 spaces", no)
        lines.append(Line(indent, s.strip(), no))
    return lines


def split_top_level(s: str, sep: str = ",") -> list[str]:
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            cur.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            cur.append(ch)
        elif ch in "(<[":
            depth += 1
            cur.append(ch)
        elif ch in ")>]":
            depth -= 1
            cur.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur or s.strip():
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


def parse_params(s: str, line: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in split_top_level(s):
        if ":" not in p:
            raise ProutError(f"bad parameter {p!r}; expected name: Type", line)
        n, t = p.split(":", 1)
        out.append((n.strip(), t.strip()))
    return out


def parse_effects(s: str, line: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in split_top_level(s):
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*\(\s*([^)]*)\s*\)", p.strip())
        if not m:
            raise ProutError(f"bad effect {p!r}; expected kind(target)", line)
        out.append((m.group(1), m.group(2).strip()))
    return out


TYPE_RE = r"[A-Za-z_]\w*(?:<[^>]+>)?"


class Parser:
    def __init__(self, text: str):
        self.source_text = text
        self.lines = preprocess(text)
        self.i = 0

    def peek(self) -> Optional[Line]:
        return self.lines[self.i] if self.i < len(self.lines) else None

    def advance(self) -> Line:
        line = self.lines[self.i]
        self.i += 1
        return line

    def parse(self) -> Program:
        types: list[TypeDef] = []
        ops: list[OpDef] = []
        flows: list[FlowDef] = []
        agents: list[AgentDef] = []
        tasks: list[TaskDef] = []
        gates: list[GateDef] = []
        policies: list[PolicyDef] = []
        while self.peek():
            line = self.peek()
            if line.indent != 0:
                raise ProutError("top-level declarations must start at column 0", line.no)
            if line.text.startswith("type "):
                types.append(self.parse_type())
            elif line.text.startswith("op "):
                ops.append(self.parse_op())
            elif line.text.startswith("flow "):
                flows.append(self.parse_flow())
            elif line.text.startswith("agent "):
                agents.append(self.parse_agent())
            elif line.text.startswith("task "):
                tasks.append(self.parse_task())
            elif line.text.startswith("gate "):
                gates.append(self.parse_gate())
            elif line.text == "policy":
                policies.append(self.parse_policy())
            else:
                raise ProutError(f"unknown declaration {line.text!r}", line.no)
        return Program(types, ops, flows, self.source_text, agents, tasks, gates, policies)

    def parse_type(self) -> TypeDef:
        line = self.advance()
        m = re.fullmatch(r"type\s+([A-Za-z_]\w*)\s*\{", line.text)
        if m:
            name = m.group(1)
            fields: dict[str, str] = {}
            while True:
                nxt = self.peek()
                if nxt is None:
                    raise ProutError(f"unterminated type {name}", line.no)
                if nxt.indent == 0 and nxt.text == "}":
                    self.advance()
                    break
                if nxt.indent != 2:
                    raise ProutError("type fields must be indented by 2 spaces", nxt.no)
                fm = re.fullmatch(r"([A-Za-z_]\w*)\s*:\s*(" + TYPE_RE + r")", nxt.text)
                if not fm:
                    raise ProutError(f"bad field declaration {nxt.text!r}", nxt.no)
                fields[fm.group(1)] = fm.group(2)
                self.advance()
            return TypeDef(name, fields, False, line.no)
        m = re.fullmatch(r"type\s+([A-Za-z_]\w*)", line.text)
        if m:
            return TypeDef(m.group(1), {}, True, line.no)
        raise ProutError(f"bad type declaration {line.text!r}", line.no)

    def parse_op(self) -> OpDef:
        line = self.advance()
        m = re.fullmatch(
            r"op\s+([A-Za-z_][\w.]*)\s*\((.*)\)\s*->\s*(" + TYPE_RE + r")",
            line.text,
        )
        if not m:
            raise ProutError(f"bad op header {line.text!r}", line.no)
        name, params_s, ret = m.group(1), m.group(2), m.group(3)
        op = OpDef(name, parse_params(params_s, line.no), ret, line.no)
        while self.peek() and self.peek().indent == 2:
            prop = self.advance()
            text = prop.text
            if text.startswith("doc "):
                op.doc = self.parse_string(text[4:], prop.no)
            elif text.startswith("eff "):
                op.effects = parse_effects(text[4:], prop.no)
            elif text.startswith("pre "):
                op.pres.append(parse_expr(text[4:], prop.no))
            elif text.startswith("post "):
                op.posts.append(parse_expr(text[5:], prop.no))
            elif text.startswith("impl "):
                rhs = text[5:].strip()
                if rhs == "native":
                    op.native = True
                else:
                    op.impl = parse_expr(rhs, prop.no)
            else:
                raise ProutError(f"unknown op property {text!r}", prop.no)
        return op

    def parse_flow(self) -> FlowDef:
        line = self.advance()
        m = re.fullmatch(
            r"flow\s+([A-Za-z_]\w*)\s*\((.*)\)\s*->\s*(" + TYPE_RE + r")",
            line.text,
        )
        if not m:
            raise ProutError(f"bad flow header {line.text!r}", line.no)
        name, params_s, ret = m.group(1), m.group(2), m.group(3)
        flow = FlowDef(name, parse_params(params_s, line.no), ret, line.no)
        while self.peek() and self.peek().indent == 2:
            stmt = self.advance()
            text = stmt.text
            if text.startswith("doc "):
                flow.doc = self.parse_string(text[4:], stmt.no)
            elif text.startswith("summary "):
                flow.summary = self.parse_string(text[8:], stmt.no)
            elif text.startswith("eff "):
                flow.effects = parse_effects(text[4:], stmt.no)
            elif text.startswith("let "):
                m2 = re.fullmatch(r"let\s+([A-Za-z_]\w*)\s*=\s*(.+)", text)
                if not m2:
                    raise ProutError(f"bad let statement {text!r}", stmt.no)
                flow.body.append(Stmt("let", stmt.no, m2.group(1), parse_expr(m2.group(2), stmt.no)))
            elif text.startswith("return "):
                flow.body.append(Stmt("return", stmt.no, "", parse_expr(text[7:], stmt.no)))
            else:
                raise ProutError(f"unknown flow statement {text!r}", stmt.no)
        return flow

    def _props(self) -> list[Line]:
        props: list[Line] = []
        while self.peek() and self.peek().indent == 2:
            props.append(self.advance())
        return props

    def _once(self, seen: set[str], name: str, line: Line) -> None:
        if name in seen:
            raise ProutError(f"duplicate {name} in this declaration", line.no)
        seen.add(name)

    def _parse_budget(self, text: str, line: int) -> tuple[int, int]:
        matched = re.fullmatch(r"budget\s+run\s+(\S+)\s+period\s+(\S+)", text)
        if not matched:
            raise ProutError(f"bad budget {text!r}; expected budget run N period N", line)
        run_text, period_text = matched.group(1), matched.group(2)
        if "." in run_text or "." in period_text:
            raise ProutError("budget must be an integer number of yen", line)
        if not re.fullmatch(r"\d+", run_text) or not re.fullmatch(r"\d+", period_text):
            raise ProutError("budget must be an integer number of yen", line)
        return int(run_text), int(period_text)

    def _parse_body_stmt(self, text: str, line: int) -> Stmt:
        if text.startswith("let "):
            matched = re.fullmatch(r"let\s+([A-Za-z_]\w*)\s*=\s*(.+)", text)
            if not matched:
                raise ProutError(f"bad let statement {text!r}", line)
            return Stmt("let", line, matched.group(1), parse_expr(matched.group(2), line))
        if text.startswith("return "):
            return Stmt("return", line, "", parse_expr(text[7:], line))
        raise ProutError(f"unknown task statement {text!r}", line)

    def parse_agent(self) -> AgentDef:
        line = self.advance()
        matched = re.fullmatch(r"agent\s+([A-Za-z_]\w*)", line.text)
        if not matched:
            raise ProutError(f"bad agent header {line.text!r}", line.no)
        agent = AgentDef(matched.group(1), line.no)
        seen: set[str] = set()
        for prop in self._props():
            text = prop.text
            if text.startswith("doc "):
                self._once(seen, "doc", prop)
                agent.doc = self.parse_string(text[4:], prop.no)
            elif text == "allow" or text.startswith("allow "):
                self._once(seen, "allow", prop)
                agent.allow_declared = True
                agent.allow = parse_effects(text[5:].strip(), prop.no)
            elif text.startswith("budget "):
                self._once(seen, "budget", prop)
                agent.budget_run, agent.budget_period = self._parse_budget(text, prop.no)
            elif text.startswith("limit "):
                limit = re.fullmatch(r"limit\s+(net|emit)\s+(\d+)\s+hour", text)
                if not limit:
                    if "." in text:
                        raise ProutError("hourly limits must be integers", prop.no)
                    raise ProutError(f"bad limit {text!r}; expected limit net N hour or limit emit N hour", prop.no)
                slot = f"limit {limit.group(1)}"
                self._once(seen, slot, prop)
                value = int(limit.group(2))
                if limit.group(1) == "net":
                    agent.limit_net = value
                else:
                    agent.limit_emit = value
            elif text.startswith("audit "):
                self._once(seen, "audit", prop)
                agent.audit = text[6:].strip()
            else:
                raise ProutError(f"unknown agent property {text!r}", prop.no)
        return agent

    def parse_task(self) -> TaskDef:
        line = self.advance()
        matched = re.fullmatch(
            r"task\s+([A-Za-z_]\w*)\s*\((.*)\)\s*->\s*(" + TYPE_RE + r")",
            line.text,
        )
        if not matched:
            raise ProutError(f"bad task header {line.text!r}", line.no)
        name, params_s, ret = matched.group(1), matched.group(2), matched.group(3)
        task = TaskDef(name, parse_params(params_s, line.no), ret, line.no)
        seen: set[str] = set()
        for prop in self._props():
            text = prop.text
            if text.startswith("doc "):
                self._once(seen, "doc", prop)
                task.doc = self.parse_string(text[4:], prop.no)
            elif text.startswith("agent "):
                self._once(seen, "agent", prop)
                agent = re.fullmatch(r"agent\s+([A-Za-z_]\w*)", text)
                if not agent:
                    raise ProutError(f"bad agent reference {text!r}", prop.no)
                task.agent_declared = True
                task.agent = agent.group(1)
            elif text.startswith("trigger "):
                self._once(seen, "trigger", prop)
                task.trigger_declared = True
                task.trigger_kind, task.trigger_value = self._parse_trigger(text, prop.no)
            elif text == "eff" or text.startswith("eff "):
                self._once(seen, "eff", prop)
                task.eff_declared = True
                task.effects = parse_effects(text[3:].strip(), prop.no)
            elif text.startswith("gate "):
                names = [part.strip() for part in text[5:].split(",")]
                if not names or any(re.fullmatch(r"[A-Za-z_]\w*", part) is None for part in names):
                    raise ProutError(f"bad gate list {text!r}", prop.no)
                task.gates.extend(names)
            elif text.startswith("key "):
                self._once(seen, "key", prop)
                key = re.fullmatch(r"key\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)", text)
                if not key:
                    raise ProutError(f"bad idempotency key {text!r}", prop.no)
                task.key_declared = True
                task.key = key.group(1)
            elif text.startswith("retry "):
                self._once(seen, "retry", prop)
                retry = re.fullmatch(r"retry\s+(\d+)\s+escalate\s+([A-Za-z_]\w*)", text)
                if not retry:
                    raise ProutError(f"bad retry {text!r}; expected retry N escalate role", prop.no)
                task.retry_declared = True
                task.retry = int(retry.group(1))
                task.escalate = retry.group(2)
            elif text.startswith("budget "):
                self._once(seen, "budget", prop)
                task.budget_run, task.budget_period = self._parse_budget(text, prop.no)
            elif text.startswith("let ") or text.startswith("return "):
                task.body.append(self._parse_body_stmt(text, prop.no))
            else:
                raise ProutError(f"unknown task property {text!r}", prop.no)
        return task

    def _parse_trigger(self, text: str, line: int) -> tuple[str, str]:
        schedule = re.fullmatch(r'trigger\s+schedule\s+("(?:\\.|[^"\\])*")', text)
        if schedule:
            return "schedule", self.parse_string(schedule.group(1), line)
        record = re.fullmatch(r"trigger\s+record\s+([A-Za-z_]\w*)", text)
        if record:
            return "record", record.group(1)
        webhook = re.fullmatch(r'trigger\s+webhook\s+("(?:\\.|[^"\\])*")', text)
        if webhook:
            return "webhook", self.parse_string(webhook.group(1), line)
        raise ProutError(
            "bad trigger; expected schedule \"...\", record Type, or webhook \"...\"",
            line,
        )

    def parse_gate(self) -> GateDef:
        line = self.advance()
        matched = re.fullmatch(r"gate\s+([A-Za-z_]\w*)", line.text)
        if not matched:
            raise ProutError(f"bad gate header {line.text!r}", line.no)
        gate = GateDef(matched.group(1), line.no)
        seen: set[str] = set()
        for prop in self._props():
            text = prop.text
            if text.startswith("doc "):
                self._once(seen, "doc", prop)
                gate.doc = self.parse_string(text[4:], prop.no)
            elif text.startswith("approver "):
                self._once(seen, "approver", prop)
                approver = re.fullmatch(r"approver\s+([A-Za-z_]\w*)", text)
                if not approver:
                    raise ProutError(f"bad approver {text!r}", prop.no)
                gate.approver = approver.group(1)
            elif text == "before" or text.startswith("before "):
                self._once(seen, "before", prop)
                gate.before = parse_effects(text[6:].strip(), prop.no)
            elif text.startswith("on_approved "):
                self._once(seen, "on_approved", prop)
                hook = re.fullmatch(r"on_approved\s+([A-Za-z_][\w.]*)", text)
                if not hook:
                    raise ProutError(f"bad on_approved {text!r}", prop.no)
                gate.on_approved = hook.group(1)
            else:
                raise ProutError(f"unknown gate property {text!r}", prop.no)
        return gate

    def parse_policy(self) -> PolicyDef:
        line = self.advance()
        policy = PolicyDef(line.no)
        seen: set[str] = set()
        for prop in self._props():
            text = prop.text
            if text == "external" or text.startswith("external "):
                self._once(seen, "external", prop)
                policy.external = parse_effects(text[8:].strip(), prop.no)
            elif text == "payment" or text.startswith("payment "):
                self._once(seen, "payment", prop)
                policy.payment = parse_effects(text[7:].strip(), prop.no)
            elif text == "contract" or text.startswith("contract "):
                self._once(seen, "contract", prop)
                policy.contract = parse_effects(text[8:].strip(), prop.no)
            else:
                raise ProutError(f"unknown policy property {text!r}", prop.no)
        return policy

    @staticmethod
    def parse_string(s: str, line: int) -> str:
        s = s.strip()
        if not (s.startswith('"') and s.endswith('"')):
            raise ProutError("expected a quoted string", line)
        return json.loads(s)


# ---------------------------------------------------------------------------
# Three-valued evaluation and linear arithmetic
# ---------------------------------------------------------------------------

def expr_key(e: Expr) -> str:
    if e.kind == "var":
        return e.name
    if e.kind == "field":
        return expr_key(e.base) + "." + e.name  # type: ignore[arg-type]
    if e.kind == "lit":
        return repr(e.value)
    if e.kind == "call":
        return e.name + "(" + ",".join(expr_key(a) for a in e.args) + ")"
    if e.kind == "bin":
        return "(" + expr_key(e.left) + e.name + expr_key(e.right) + ")"  # type: ignore[arg-type]
    if e.kind == "unary":
        return "(" + e.name + expr_key(e.left) + ")"  # type: ignore[arg-type]
    return "?"


def fmt_expr(e: Optional[Expr]) -> str:
    if e is None:
        return "?"
    if e.kind == "var":
        return e.name
    if e.kind == "field":
        return fmt_expr(e.base) + "." + e.name
    if e.kind == "lit":
        if e.name == "str":
            return json.dumps(e.value, ensure_ascii=False)
        if e.name == "bool":
            return "true" if e.value else "false"
        return str(e.value)
    if e.kind == "call":
        return e.name + "(" + ", ".join(fmt_expr(a) for a in e.args) + ")"
    if e.kind == "bin":
        return f"({fmt_expr(e.left)} {e.name} {fmt_expr(e.right)})"
    if e.kind == "unary":
        return f"({e.name}{fmt_expr(e.left)})"
    return "?"


Number = Union[int, Decimal]


def add_coeffs(a: dict[str, Number], b: dict[str, Number]) -> dict[str, Number]:
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + v
    return out


def neg_coeffs(a: dict[str, Number]) -> dict[str, Number]:
    return {k: -v for k, v in a.items()}


def scale_coeffs(a: dict[str, Number], k: Number) -> dict[str, Number]:
    return {key: v * k for key, v in a.items()}


def linear(e: Optional[Expr]) -> Optional[tuple[dict[str, Number], Number]]:
    """Return (coefficients over atomic expressions, constant), or None.

    Integers stay ints. Decimal literals use decimal.Decimal. A date literal
    is its Gregorian ordinal so that date +/- days and date - date are affine;
    the ordinal origin cancels in any well-typed comparison. Division is not
    affine and stays unknown, including decimal division.
    """
    if e is None:
        return None
    if e.kind == "lit" and e.name == "int":
        return ({}, int(e.value))
    if e.kind == "lit" and e.name == "dec":
        return ({}, e.value if isinstance(e.value, Decimal) else Decimal(str(e.value)))
    if e.kind == "lit" and e.name == "date":
        return ({}, e.value.toordinal())
    if e.kind in ("var", "field"):
        return ({expr_key(e): 1}, 0)
    if e.kind == "bin":
        if e.name == "+":
            l = linear(e.left)
            r = linear(e.right)
            if l is None or r is None:
                return None
            return (add_coeffs(l[0], r[0]), l[1] + r[1])
        if e.name == "-":
            l = linear(e.left)
            r = linear(e.right)
            if l is None or r is None:
                return None
            return (add_coeffs(l[0], neg_coeffs(r[0])), l[1] - r[1])
        if e.name == "*":
            l = linear(e.left)
            r = linear(e.right)
            if l is None or r is None:
                return None
            if not l[0]:
                return (scale_coeffs(r[0], l[1]), r[1] * l[1])
            if not r[0]:
                return (scale_coeffs(l[0], r[1]), l[1] * r[1])
            return None
    return None


def affine_difference(left: Optional[Expr], right: Optional[Expr]) -> Optional[Number]:
    """Constant value of left - right when both sides are affine, else None.

    None means the checker cannot decide. Callers must report that, not treat
    it as success.
    """
    l = linear(left)
    r = linear(right)
    if l is None or r is None:
        return None
    coeffs = add_coeffs(l[0], neg_coeffs(r[0]))
    if any(v != 0 for v in coeffs.values()):
        return None
    return l[1] - r[1]


def evaluate(e: Optional[Expr]) -> Optional[bool]:
    """Three-valued evaluation: True, False, or None (unknown)."""
    if e is None:
        return None
    if e.kind == "lit":
        if e.name == "bool":
            return bool(e.value)
        return None
    if e.kind == "unary":
        if e.name == "!":
            v = evaluate(e.left)
            return None if v is None else not v
        return None
    if e.kind == "bin":
        op = e.name
        if op == "&&":
            l = evaluate(e.left)
            r = evaluate(e.right)
            if l is False or r is False:
                return False
            if l is True and r is True:
                return True
            return None
        if op == "||":
            l = evaluate(e.left)
            r = evaluate(e.right)
            if l is True or r is True:
                return True
            if l is False and r is False:
                return False
            return None
        if op in ("==", "!="):
            l = e.left
            r = e.right
            if l is not None and r is not None and l.kind == "lit" and r.kind == "lit":
                eq = l.value == r.value
                return eq if op == "==" else not eq
            if l is not None and r is not None and expr_key(l) == expr_key(r):
                return op == "=="
            const = affine_difference(l, r)
            if const is None:
                return None
            eq = const == 0
            return eq if op == "==" else not eq
        if op in ("<", "<=", ">", ">="):
            const = affine_difference(e.left, e.right)
            if const is None:
                return None
            if op == "<=":
                return const <= 0
            if op == "<":
                return const < 0
            if op == ">=":
                return const >= 0
            if op == ">":
                return const > 0
    return None


def subst(e: Optional[Expr], mapping: dict[str, Expr]) -> Optional[Expr]:
    if e is None:
        return None
    if e.kind == "var":
        return mapping.get(e.name, e)
    if e.kind == "field":
        return Expr("field", line=e.line, base=subst(e.base, mapping), name=e.name)
    if e.kind == "call":
        return Expr("call", line=e.line, name=e.name, args=[subst(a, mapping) for a in e.args])  # type: ignore[list-item]
    if e.kind == "unary":
        return Expr("unary", line=e.line, name=e.name, left=subst(e.left, mapping))
    if e.kind == "bin":
        return Expr("bin", line=e.line, name=e.name, left=subst(e.left, mapping), right=subst(e.right, mapping))
    return e


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

BUILTIN_TYPES = {"Int", "String", "Bool", "Date", "Decimal"}
EFFECT_KINDS = {"read", "write", "net", "spend", "emit"}
# Period boundaries for spend. Not a per-program setting in this slice.
PERIOD_TIMEZONE = "Asia/Tokyo"
RETRY_MAX = 5
_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_WEBHOOK_RE = re.compile(r"[A-Za-z_][\w./-]*\Z")


def _cron_atom(part: str, lo: int, hi: int) -> bool:
    """One numeric cron atom: `*`, `n`, `a-b`, or those with `/step`. Names are rejected."""
    step: Optional[int] = None
    if "/" in part:
        base, step_text = part.split("/", 1)
        if not step_text.isdigit() or int(step_text) == 0:
            return False
        step = int(step_text)
        part = base
    if part == "*":
        return step is None or step <= (hi - lo + 1)
    if "-" in part:
        start_text, end_text = part.split("-", 1)
        if not (start_text.isdigit() and end_text.isdigit()):
            return False
        start, end = int(start_text), int(end_text)
        if start > end or start < lo or end > hi:
            return False
        return step is None or step <= (end - start + 1)
    if part.isdigit() and lo <= int(part) <= hi:
        return step is None
    return False


def cron_ok(value: str) -> bool:
    """Five numeric fields: minute hour day-of-month month day-of-week."""
    fields = value.strip().split()
    if len(fields) != 5:
        return False
    for field_text, (lo, hi) in zip(fields, _CRON_RANGES):
        if not field_text or any(not _cron_atom(part, lo, hi) for part in field_text.split(",")):
            return False
    return True


def format_effect(e: tuple[str, str]) -> str:
    return f"{e[0]}({e[1]})"


class Checker:
    def __init__(self, program: Program, allow_net: Optional[list[str]] = None, deny_net: Optional[list[str]] = None):
        self.program = program
        self.diags: list[Diagnostic] = []
        self.types = {t.name: t for t in program.types}
        for b in BUILTIN_TYPES:
            self.types.setdefault(b, TypeDef(b, {}, True))
        self.ops = {o.name: o for o in program.ops}
        self.flows = {f.name: f for f in program.flows}
        self.allow_net = set(allow_net) if allow_net else {"stripe"}
        self.deny_net = set(deny_net or [])

    def error(self, code: str, message: str, line: int = 0, fix: Optional[str] = None, detail: Optional[str] = None) -> None:
        self.diags.append(Diagnostic(code, "error", message, line, fix, detail))

    def warn(self, code: str, message: str, line: int = 0, fix: Optional[str] = None, detail: Optional[str] = None) -> None:
        self.diags.append(Diagnostic(code, "warning", message, line, fix, detail))

    def check(self) -> list[Diagnostic]:
        self.check_duplicates()
        for t in self.program.types:
            self.check_type(t)
        for op in self.program.ops:
            self.check_op(op)
        for flow in self.program.flows:
            self.check_flow(flow)
        for op in self.program.ops:
            self.check_policy(op)
        self.check_ai_tasks()
        return self.diags

    def check_duplicates(self) -> None:
        seen: dict[str, int] = {}
        for t in self.program.types:
            if t.name in seen:
                self.error("E-DUP", f"duplicate type {t.name!r}", t.line)
            seen[t.name] = t.line
        seen_ops: dict[str, int] = {}
        for op in self.program.ops:
            if op.name in seen_ops:
                self.error("E-DUP", f"duplicate operation {op.name!r}", op.line)
            seen_ops[op.name] = op.line
        seen_flows: dict[str, int] = {}
        for flow in self.program.flows:
            if flow.name in seen_flows:
                self.error("E-DUP", f"duplicate flow {flow.name!r}", flow.line)
            seen_flows[flow.name] = flow.line

    def check_type(self, t: TypeDef) -> None:
        for fname, ftype in t.fields.items():
            self.check_type_exists(ftype, t.line, f"field {t.name}.{fname}")

    def check_type_exists(self, tname: str, line: int, where: str = "") -> None:
        base = re.sub(r"<.*>", "", tname).strip()
        if base not in self.types and base not in ("Unknown",):
            self.error("E-TYPE", f"unknown type {tname!r}" + (f" in {where}" if where else ""), line)

    def type_of(self, e: Optional[Expr], env: dict[str, str]) -> str:
        if e is None:
            return "Unknown"
        if e.kind == "lit":
            return {"int": "Int", "str": "String", "bool": "Bool", "date": "Date", "dec": "Decimal"}.get(e.name, "Unknown")
        if e.kind == "var":
            if e.name in env:
                return env[e.name]
            self.error("E-UNDEF", f"unknown variable {e.name!r}", e.line)
            return "Unknown"
        if e.kind == "field":
            bt = self.type_of(e.base, env)
            t = self.types.get(bt)
            if t is None or e.name not in t.fields:
                self.error("E-FIELD", f"type {bt} has no field {e.name!r}", e.line)
                return "Unknown"
            return t.fields[e.name]
        if e.kind == "call":
            op = self.ops.get(e.name)
            if op is None:
                self.error("E-UNDEF", f"unknown operation {e.name!r}", e.line)
                return "Unknown"
            if len(e.args) != len(op.params):
                self.error("E-ARITY", f"{e.name} expects {len(op.params)} argument(s), got {len(e.args)}", e.line)
                return op.ret
            for (pname, ptype), arg in zip(op.params, e.args):
                at = self.type_of(arg, env)
                if at != "Unknown" and not self.compatible(ptype, at):
                    self.error("E-TYPE", f"argument {pname!r} of {e.name} expects {ptype}, got {at}", arg.line or e.line)
            return op.ret
        if e.kind == "unary":
            lt = self.type_of(e.left, env)
            if e.name == "!":
                if lt not in ("Bool", "Unknown"):
                    self.error("E-TYPE", f"operator ! expects a Bool operand, got {lt}", e.line)
                return "Bool"
            return "Unknown"
        if e.kind == "bin":
            op = e.name
            if op in ("==", "!="):
                lt = self.type_of(e.left, env)
                rt = self.type_of(e.right, env)
                if (
                    lt != "Unknown"
                    and rt != "Unknown"
                    and not self.compatible(lt, rt)
                    and not self.compatible(rt, lt)
                ):
                    self.error("E-TYPE", f"cannot compare {lt} and {rt} with {op}", e.line)
                return "Bool"
            if op in ("<", "<=", ">", ">="):
                lt = self.type_of(e.left, env)
                rt = self.type_of(e.right, env)
                if lt != "Unknown" and rt != "Unknown":
                    orderable = (
                        {lt, rt} <= {"Int", "Money"}
                        or {lt, rt} <= {"Int", "Decimal"}
                        or (lt == rt and lt in ("Int", "Money", "String", "Date", "Decimal"))
                    )
                    if not orderable:
                        self.error("E-TYPE", f"cannot compare {lt} and {rt} with {op}", e.line)
                return "Bool"
            lt = self.type_of(e.left, env)
            rt = self.type_of(e.right, env)
            if op in ("&&", "||"):
                if lt not in ("Bool", "Unknown") or rt not in ("Bool", "Unknown"):
                    self.error("E-TYPE", f"operator {op} expects Bool operands, got {lt} and {rt}", e.line)
                return "Bool"
            if op == "+":
                if lt == "Unknown" or rt == "Unknown":
                    return "Unknown"
                if lt == "Date" and rt == "Date":
                    self.error("E-TYPE", "cannot add Date and Date", e.line)
                    return "Unknown"
                if (lt == "Date" and rt == "Int") or (lt == "Int" and rt == "Date"):
                    return "Date"
                if lt == rt:
                    return lt
                if {lt, rt} == {"Money", "Int"}:
                    return "Money"
                if {lt, rt} <= {"Int", "Decimal"}:
                    return "Decimal"
                self.error("E-TYPE", f"cannot add {lt} and {rt}", e.line)
                return "Unknown"
            if op == "-":
                if lt == "Unknown" or rt == "Unknown":
                    return "Unknown"
                if lt == "Date" and rt == "Date":
                    return "Int"
                if lt == "Date" and rt == "Int":
                    return "Date"
                if lt == "Money" and rt in ("Money", "Int"):
                    return "Money"
                if lt == "Int" and rt == "Int":
                    return "Int"
                if {lt, rt} <= {"Int", "Decimal"}:
                    return "Decimal"
                self.error("E-TYPE", f"cannot subtract {rt} from {lt}", e.line)
                return "Unknown"
            if op == "*":
                if lt == "Unknown" or rt == "Unknown":
                    return "Unknown"
                if lt == "Int" and rt == "Int":
                    return "Int"
                if {lt, rt} <= {"Int", "Decimal"}:
                    return "Decimal"
                if {lt, rt} == {"Money", "Int"}:
                    return "Money"
                self.error("E-TYPE", f"cannot multiply {lt} and {rt}", e.line)
                return "Unknown"
            if op == "/":
                if lt == "Unknown" or rt == "Unknown":
                    return "Unknown"
                if lt == "Int" and rt == "Int":
                    return "Int"
                if {lt, rt} <= {"Int", "Decimal"}:
                    return "Decimal"
                if lt == "Money" and rt == "Int":
                    return "Money"
                self.error("E-TYPE", f"cannot divide {lt} by {rt}", e.line)
                return "Unknown"
        return "Unknown"

    @staticmethod
    def compatible(expected: str, actual: str) -> bool:
        if expected == actual:
            return True
        if {expected, actual} <= {"Int", "Decimal"}:
            return True
        if expected == "Status" and actual == "String":
            return True
        return False

    def calls_in(self, e: Optional[Expr]) -> list[Expr]:
        if e is None:
            return []
        if e.kind == "call":
            out = [e]
            for a in e.args:
                out.extend(self.calls_in(a))
            return out
        if e.kind == "bin":
            return self.calls_in(e.left) + self.calls_in(e.right)
        if e.kind == "unary":
            return self.calls_in(e.left)
        if e.kind == "field":
            return self.calls_in(e.base)
        return []

    def effects_of_expr(self, e: Optional[Expr]) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for c in self.calls_in(e):
            op = self.ops.get(c.name)
            if op:
                out |= set(op.effects)
        return out

    def check_op(self, op: OpDef) -> None:
        env = {n: t for n, t in op.params}
        for n, t in op.params:
            self.check_type_exists(t, op.line, f"parameter {n}")
        self.check_type_exists(op.ret, op.line, "return type")
        for kind, target in op.effects:
            if kind not in EFFECT_KINDS:
                self.error("E-EFF", f"unknown effect kind {kind!r}", op.line)
            if not target:
                self.error("E-EFF", f"effect {kind} has no target", op.line)
        for p in op.pres:
            t = self.type_of(p, env)
            if t not in ("Bool", "Unknown"):
                self.error("E-TYPE", f"precondition must be Bool, got {t}", p.line)
        for p in op.posts:
            env2 = dict(env)
            env2["result"] = op.ret
            t = self.type_of(p, env2)
            if t not in ("Bool", "Unknown"):
                self.error("E-TYPE", f"postcondition must be Bool, got {t}", p.line)
        if op.impl is not None:
            it = self.type_of(op.impl, env)
            if it != "Unknown" and not self.compatible(op.ret, it):
                self.error("E-TYPE", f"impl returns {it}, expected {op.ret}", op.impl.line)
            inferred = self.effects_of_expr(op.impl)
            declared = set(op.effects)
            if declared != inferred:
                missing = inferred - declared
                extra = declared - inferred
                bits = []
                if missing:
                    bits.append("missing " + ", ".join(format_effect(e) for e in sorted(missing)))
                if extra:
                    bits.append("unused " + ", ".join(format_effect(e) for e in sorted(extra)))
                self.error("E-EFF", f"operation {op.name} effects do not match impl ({'; '.join(bits)})", op.line)

    def check_flow(self, flow: FlowDef) -> None:
        env = {n: t for n, t in flow.params}
        for n, t in flow.params:
            self.check_type_exists(t, flow.line, f"parameter {n}")
        self.check_type_exists(flow.ret, flow.line, "return type")
        inferred: set[tuple[str, str]] = set()
        seen_return = False
        for stmt in flow.body:
            if seen_return:
                self.error(
                    "E-FLOW",
                    f"statement after return in flow {flow.name} is unreachable",
                    stmt.line,
                    fix="move this statement before the return, or remove it",
                )
                continue
            if stmt.kind == "let":
                t = self.type_of(stmt.expr, env)
                if stmt.name in env:
                    self.error("E-DUP", f"variable {stmt.name!r} is already defined", stmt.line)
                env[stmt.name] = t
                inferred |= self.effects_of_expr(stmt.expr)
                for c in self.calls_in(stmt.expr):
                    self.check_call_contracts(c, env)
            elif stmt.kind == "return":
                seen_return = True
                t = self.type_of(stmt.expr, env)
                if t != "Unknown" and not self.compatible(flow.ret, t):
                    self.error("E-TYPE", f"return expects {flow.ret}, got {t}", stmt.line)
                inferred |= self.effects_of_expr(stmt.expr)
                for c in self.calls_in(stmt.expr):
                    self.check_call_contracts(c, env)
        if not seen_return:
            self.error(
                "E-FLOW",
                f"flow {flow.name} must end with a return statement",
                flow.line,
                fix=f"add a return statement that produces {flow.ret}",
            )
        declared = set(flow.effects)
        if declared != inferred:
            missing = inferred - declared
            extra = declared - inferred
            bits = []
            if missing:
                bits.append("missing " + ", ".join(format_effect(e) for e in sorted(missing)))
            if extra:
                bits.append("unused " + ", ".join(format_effect(e) for e in sorted(extra)))
            fix = None
            if inferred:
                fix = "eff " + ", ".join(format_effect(e) for e in sorted(inferred))
            self.error("E-EFF", f"flow {flow.name} effects do not match implementation ({'; '.join(bits)})", flow.line, fix)

    def check_call_contracts(self, c: Expr, env: dict[str, str]) -> None:
        op = self.ops.get(c.name)
        if not op:
            return
        if len(c.args) != len(op.params):
            return
        mapping = {p: a for (p, _), a in zip(op.params, c.args)}
        for pre in op.pres:
            inst = subst(pre, mapping)
            val = evaluate(inst)
            if val is False:
                self.error(
                    "E-PRE",
                    f"precondition of {c.name} is false: {fmt_expr(inst)}",
                    c.line,
                    fix=f"make {fmt_expr(inst)} true, or add a guard before the call",
                )
            elif val is None:
                self.warn(
                    "W-PRE",
                    f"precondition of {c.name} needs a runtime guard: {fmt_expr(inst)}",
                    c.line,
                    detail="The checker could not prove this statically. It must be enforced at runtime.",
                )

    def check_policy(self, op: OpDef) -> None:
        for kind, target in op.effects:
            if kind == "net":
                if target in self.deny_net:
                    self.error("E-POLICY", f"network target {target!r} is denied by policy", op.line)
                elif target not in self.allow_net:
                    self.error("E-POLICY", f"network target {target!r} is not allowed by policy", op.line)

    def check_ai_tasks(self) -> None:
        """Static envelope for agent and task. No counters, cron, or idempotency store."""
        agents = {agent.name: agent for agent in self.program.agents}
        gates = {gate.name: gate for gate in self.program.gates}
        self._check_ai_duplicates()
        external, contract = self._check_ai_policy()
        for agent in self.program.agents:
            self._check_agent_envelope(agent)
        for gate in self.program.gates:
            self._check_gate_decl(gate, agents)
        for task in self.program.tasks:
            self._check_one_task(task, agents, gates, external, contract)

    def _check_ai_duplicates(self) -> None:
        seen_agents: dict[str, int] = {}
        for agent in self.program.agents:
            if agent.name in seen_agents:
                self.error("E-DUP", f"duplicate agent {agent.name!r}", agent.line)
            seen_agents[agent.name] = agent.line
        seen_tasks: dict[str, int] = {}
        for task in self.program.tasks:
            if task.name in seen_tasks:
                self.error("E-DUP", f"duplicate task {task.name!r}", task.line)
            seen_tasks[task.name] = task.line
        seen_gates: dict[str, int] = {}
        for gate in self.program.gates:
            if gate.name in seen_gates:
                self.error("E-DUP", f"duplicate gate {gate.name!r}", gate.line)
            seen_gates[gate.name] = gate.line
        if len(self.program.policies) > 1:
            self.error("E-DUP", "duplicate policy", self.program.policies[1].line)

    def _check_effect_list(self, effects: list[tuple[str, str]], line: int, where: str) -> None:
        seen: set[tuple[str, str]] = set()
        for effect in effects:
            kind, target = effect
            if kind not in EFFECT_KINDS:
                self.error("E-EFF", f"unknown effect kind {kind!r}", line)
            if not target:
                self.error("E-EFF", f"effect {kind} has no target", line)
            if effect in seen:
                self.error("E-DUP", f"duplicate effect {format_effect(effect)} in {where}", line)
            seen.add(effect)

    def _check_ai_policy(self) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
        external: set[tuple[str, str]] = set()
        contract: set[tuple[str, str]] = set()
        for policy in self.program.policies:
            self._check_effect_list(policy.external, policy.line, "policy external")
            self._check_effect_list(policy.payment, policy.line, "policy payment")
            self._check_effect_list(policy.contract, policy.line, "policy contract")
            for effect in policy.external:
                if effect[0] not in ("emit", "net"):
                    self.error(
                        "E-POLICY",
                        f"policy external accepts emit or net, got {format_effect(effect)}",
                        policy.line,
                    )
            for effect in policy.payment:
                if effect[0] != "spend":
                    self.error(
                        "E-POLICY",
                        f"policy payment accepts spend, got {format_effect(effect)}",
                        policy.line,
                    )
            for effect in policy.contract:
                if effect[0] != "write":
                    self.error(
                        "E-POLICY",
                        f"policy contract accepts write, got {format_effect(effect)}",
                        policy.line,
                    )
            external |= set(policy.external)
            contract |= set(policy.contract)
        return external, contract

    def _check_budget_pair(self, where: str, line: int, has_spend: bool, run: int, period: int) -> None:
        if has_spend:
            if run <= 0 or period <= 0:
                self.error(
                    "E-POLICY",
                    f"{where}: spend effect needs a positive per-run and per-period budget",
                    line,
                )
            elif run > period:
                self.error(
                    "E-POLICY",
                    f"{where}: per-run spend exceeds the period budget ({run} <= {period} is false)",
                    line,
                )
        elif run != 0 or period != 0:
            self.error(
                "E-POLICY",
                f"{where}: spend budget is set but spend is not an allowed effect",
                line,
            )

    def _check_rate_limit(self, agent: AgentDef, kind: str, limit: int) -> None:
        has_kind = any(effect[0] == kind for effect in agent.allow)
        if has_kind and limit <= 0:
            self.error(
                "E-POLICY",
                f"agent {agent.name}: {kind} effect needs a positive hourly rate limit",
                agent.line,
            )
        elif not has_kind and limit != 0:
            self.error(
                "E-POLICY",
                f"agent {agent.name}: {kind} rate limit is set but {kind} is not an allowed effect",
                agent.line,
            )

    def _check_agent_envelope(self, agent: AgentDef) -> None:
        if not agent.allow_declared:
            self.error("E-POLICY", f"agent {agent.name} requires an allow list", agent.line)
        self._check_effect_list(agent.allow, agent.line, f"agent {agent.name}")
        if agent.audit != "ast_hash":
            self.error("E-POLICY", f"agent {agent.name}: audit ast_hash is required", agent.line)
        has_spend = any(effect[0] == "spend" for effect in agent.allow)
        self._check_budget_pair(
            f"agent {agent.name}",
            agent.line,
            has_spend,
            agent.budget_run,
            agent.budget_period,
        )
        self._check_rate_limit(agent, "net", agent.limit_net)
        self._check_rate_limit(agent, "emit", agent.limit_emit)

    def _check_gate_decl(self, gate: GateDef, agents: dict[str, AgentDef]) -> None:
        if not gate.approver:
            self.error("E-POLICY", f"gate {gate.name} requires an approver", gate.line)
        elif gate.approver in agents:
            self.error(
                "E-POLICY",
                f"gate {gate.name}: approver {gate.approver!r} is an agent; approval must be a human role",
                gate.line,
            )
        if not gate.before:
            self.error("E-POLICY", f"gate {gate.name} requires before", gate.line)
        self._check_effect_list(gate.before, gate.line, f"gate {gate.name}")
        if gate.on_approved and gate.on_approved not in self.ops:
            self.error("E-UNDEF", f"unknown operation {gate.on_approved!r}", gate.line)

    def _needs_gate(self, effect: tuple[str, str], external: set[tuple[str, str]], contract: set[tuple[str, str]]) -> bool:
        kind, _target = effect
        if kind == "spend":
            return True
        if kind in ("emit", "net") and effect in external:
            return True
        if kind == "write" and effect in contract:
            return True
        return False

    def _gate_gap_message(self, effect: tuple[str, str], external: set[tuple[str, str]]) -> str:
        kind, _target = effect
        shown = format_effect(effect)
        if kind == "emit" and effect in external:
            return f"emit to an external party has no approval gate ({shown})"
        if kind == "net" and effect in external:
            return f"net to an external party has no approval gate ({shown})"
        if kind == "spend":
            return f"spend has no approval gate ({shown})"
        return f"a contract change has no approval gate ({shown})"

    def _check_task_body(self, task: TaskDef) -> set[tuple[str, str]]:
        env = {name: typ for name, typ in task.params}
        for name, typ in task.params:
            self.check_type_exists(typ, task.line, f"parameter {name}")
        self.check_type_exists(task.ret, task.line, "return type")
        inferred: set[tuple[str, str]] = set()
        seen_return = False
        for stmt in task.body:
            if seen_return:
                self.error(
                    "E-FLOW",
                    f"statement after return in task {task.name} is unreachable",
                    stmt.line,
                    fix="move this statement before the return, or remove it",
                )
                continue
            if stmt.kind == "let":
                typ = self.type_of(stmt.expr, env)
                if stmt.name in env:
                    self.error("E-DUP", f"variable {stmt.name!r} is already defined", stmt.line)
                env[stmt.name] = typ
                inferred |= self.effects_of_expr(stmt.expr)
                for call in self.calls_in(stmt.expr):
                    self.check_call_contracts(call, env)
            elif stmt.kind == "return":
                seen_return = True
                typ = self.type_of(stmt.expr, env)
                if typ != "Unknown" and not self.compatible(task.ret, typ):
                    self.error("E-TYPE", f"return expects {task.ret}, got {typ}", stmt.line)
                inferred |= self.effects_of_expr(stmt.expr)
                for call in self.calls_in(stmt.expr):
                    self.check_call_contracts(call, env)
        if not seen_return:
            self.error(
                "E-FLOW",
                f"task {task.name} must end with a return statement",
                task.line,
                fix=f"add a return statement that produces {task.ret}",
            )
        return inferred

    def _check_task_key(self, task: TaskDef) -> None:
        if not task.key_declared:
            self.error("E-POLICY", f"task {task.name}: idempotency key is required", task.line)
            return
        params = {name: typ for name, typ in task.params}
        if "." not in task.key:
            if task.key not in params:
                self.error(
                    "E-POLICY",
                    f"task {task.name}: idempotency key {task.key!r} is not a parameter or a field of a parameter",
                    task.line,
                )
            return
        base, field_name = task.key.split(".", 1)
        if base not in params:
            self.error(
                "E-POLICY",
                f"task {task.name}: idempotency key {task.key!r} is not a parameter or a field of a parameter",
                task.line,
            )
            return
        type_name = re.sub(r"<.*>", "", params[base]).strip()
        owner = self.types.get(type_name)
        if owner is None or field_name not in owner.fields:
            self.error(
                "E-FIELD",
                f"type {type_name} has no field {field_name!r} for idempotency key {task.key!r}",
                task.line,
            )

    def _check_task_trigger(self, task: TaskDef) -> None:
        if not task.trigger_declared:
            self.error("E-POLICY", f"task {task.name} requires a trigger", task.line)
            return
        if task.trigger_kind == "schedule":
            if not cron_ok(task.trigger_value):
                self.error(
                    "E-POLICY",
                    "task "
                    + task.name
                    + ": schedule must be a 5-field numeric cron; names such as MON are not accepted, got "
                    + repr(task.trigger_value),
                    task.line,
                )
        elif task.trigger_kind == "record":
            self.check_type_exists(task.trigger_value, task.line, "record trigger")
        elif task.trigger_kind == "webhook":
            if _WEBHOOK_RE.fullmatch(task.trigger_value) is None:
                self.error(
                    "E-POLICY",
                    f"task {task.name}: webhook must be a receiver name, got {task.trigger_value!r}",
                    task.line,
                )

    def _check_task_budget(
        self,
        task: TaskDef,
        agent: Optional[AgentDef],
        has_spend: bool,
    ) -> None:
        if task.budget_run is None or task.budget_period is None:
            return
        self._check_budget_pair(
            f"task {task.name}",
            task.line,
            has_spend,
            task.budget_run,
            task.budget_period,
        )
        if agent is None or not has_spend:
            return
        if task.budget_run > agent.budget_run:
            self.error(
                "E-POLICY",
                f"task {task.name}: per-run spend exceeds the agent budget "
                f"({task.budget_run} <= {agent.budget_run} is false)",
                task.line,
            )
        if task.budget_period > agent.budget_period:
            self.error(
                "E-POLICY",
                f"task {task.name}: period spend exceeds the agent budget "
                f"({task.budget_period} <= {agent.budget_period} is false)",
                task.line,
            )

    def _check_escalation(
        self,
        task: TaskDef,
        agents: dict[str, AgentDef],
        external: set[tuple[str, str]],
        contract: set[tuple[str, str]],
    ) -> None:
        if not task.escalate:
            return
        target = task.escalate
        if target in agents:
            bad = [effect for effect in agents[target].allow if self._needs_gate(effect, external, contract)]
            if bad:
                listed = ", ".join(format_effect(effect) for effect in sorted(bad))
                self.error(
                    "E-POLICY",
                    f"task {task.name}: escalation path produces external effects ({listed})",
                    task.line,
                )
            else:
                self.error(
                    "E-POLICY",
                    f"task {task.name}: escalation target {target!r} is an agent; "
                    "escalation goes to a human role and must not produce external effects",
                    task.line,
                )
        op = self.ops.get(target)
        if op is None:
            return
        bad = [effect for effect in op.effects if self._needs_gate(effect, external, contract)]
        if bad:
            listed = ", ".join(format_effect(effect) for effect in sorted(bad))
            self.error(
                "E-POLICY",
                f"task {task.name}: escalation path produces external effects ({listed})",
                task.line,
            )

    def _check_one_task(
        self,
        task: TaskDef,
        agents: dict[str, AgentDef],
        gates: dict[str, GateDef],
        external: set[tuple[str, str]],
        contract: set[tuple[str, str]],
    ) -> None:
        if not task.agent_declared:
            self.error("E-POLICY", f"task {task.name} requires an agent", task.line)
        elif task.agent not in agents:
            self.error("E-UNDEF", f"unknown agent {task.agent!r}", task.line)
        if not task.eff_declared:
            self.error("E-POLICY", f"task {task.name} requires eff", task.line)
        if not task.retry_declared:
            self.error("E-POLICY", f"task {task.name} requires retry and escalate", task.line)
        elif not 0 <= task.retry <= RETRY_MAX:
            self.error(
                "E-POLICY",
                f"task {task.name}: retries must be from 0 through {RETRY_MAX}, got {task.retry}",
                task.line,
            )
        self._check_task_trigger(task)
        self._check_task_key(task)
        self._check_effect_list(task.effects, task.line, f"task {task.name}")
        inferred = self._check_task_body(task)
        declared = set(task.effects)
        if task.eff_declared and declared != inferred:
            missing = inferred - declared
            extra = declared - inferred
            bits = []
            if missing:
                bits.append("missing " + ", ".join(format_effect(effect) for effect in sorted(missing)))
            if extra:
                bits.append("unused " + ", ".join(format_effect(effect) for effect in sorted(extra)))
            fix = None
            if inferred:
                fix = "eff " + ", ".join(format_effect(effect) for effect in sorted(inferred))
            self.error(
                "E-EFF",
                f"task {task.name} effects do not match implementation ({'; '.join(bits)})",
                task.line,
                fix,
            )
        agent = agents.get(task.agent)
        effects = declared | inferred
        if agent is not None:
            outside = effects - set(agent.allow)
            if outside:
                listed = ", ".join(format_effect(effect) for effect in sorted(outside))
                self.error(
                    "E-POLICY",
                    f"task {task.name}: effect {listed} is outside agent {agent.name} allow",
                    task.line,
                    fix="allow " + ", ".join(format_effect(effect) for effect in sorted(set(agent.allow) | outside)),
                )
        has_spend = any(effect[0] == "spend" for effect in effects)
        self._check_task_budget(task, agent, has_spend)
        self._check_escalation(task, agents, external, contract)
        self._check_task_gates(task, gates, effects, external, contract)

    def _check_task_gates(
        self,
        task: TaskDef,
        gates: dict[str, GateDef],
        effects: set[tuple[str, str]],
        external: set[tuple[str, str]],
        contract: set[tuple[str, str]],
    ) -> None:
        required = [effect for effect in sorted(effects) if self._needs_gate(effect, external, contract)]
        if not required:
            if task.gates:
                self.error(
                    "E-POLICY",
                    f"task {task.name}: approval is set but this task has no gated effect",
                    task.line,
                )
            return
        if not task.gates:
            for effect in required:
                self.error(
                    "E-POLICY",
                    f"task {task.name}: {self._gate_gap_message(effect, external)}",
                    task.line,
                )
            return
        covered: dict[tuple[str, str], str] = {}
        for name in task.gates:
            gate = gates.get(name)
            if gate is None:
                self.error("E-POLICY", f"task {task.name}: approval gate {name!r} is not declared", task.line)
                continue
            hits = [effect for effect in required if effect in set(gate.before)]
            if not hits:
                shown = ", ".join(format_effect(effect) for effect in gate.before) or "(none)"
                self.error(
                    "E-POLICY",
                    f"task {task.name}: approval gate {name!r} action {shown} is not one of the gated effects",
                    task.line,
                )
                continue
            for effect in hits:
                if effect in covered:
                    self.error(
                        "E-POLICY",
                        f"task {task.name}: approval gate action {format_effect(effect)} is named more than once",
                        task.line,
                    )
                    continue
                covered[effect] = name
        for effect in required:
            if effect not in covered:
                self.error(
                    "E-POLICY",
                    f"task {task.name}: {self._gate_gap_message(effect, external)}",
                    task.line,
                )


# ---------------------------------------------------------------------------
# Canonical AST and hash
# ---------------------------------------------------------------------------

def expr_to_dict(e: Optional[Expr]) -> Optional[dict]:
    if e is None:
        return None
    d: dict[str, Any] = {"kind": e.kind}
    if e.kind == "lit":
        d["type"] = e.name
        if e.name == "date":
            d["value"] = e.value.isoformat()
        elif e.name == "dec":
            d["value"] = format(e.value, "f")
        else:
            d["value"] = e.value
    elif e.kind == "var":
        d["name"] = e.name
    elif e.kind == "field":
        d["base"] = expr_to_dict(e.base)
        d["name"] = e.name
    elif e.kind == "call":
        d["name"] = e.name
        d["args"] = [expr_to_dict(a) for a in e.args]
    elif e.kind == "bin":
        d["op"] = e.name
        d["left"] = expr_to_dict(e.left)
        d["right"] = expr_to_dict(e.right)
    elif e.kind == "unary":
        d["op"] = e.name
        d["expr"] = expr_to_dict(e.left)
    return d


def _canonical_expr_list(exprs: list[Expr]) -> list[Expr]:
    """Sort conjuncts by canonical JSON; their source order is not meaning."""
    return sorted(
        exprs,
        key=lambda e: json.dumps(
            expr_to_dict(e), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
    )


def program_to_dict(p: Program) -> dict:
    data = {
        "types": [
            {"name": t.name, "opaque": t.opaque, "fields": dict(sorted(t.fields.items()))}
            for t in sorted(p.types, key=lambda x: x.name)
        ],
        "ops": [
            {
                "name": o.name,
                "params": o.params,
                "ret": o.ret,
                "effects": sorted(o.effects),
                "pre": [expr_to_dict(x) for x in _canonical_expr_list(o.pres)],
                "post": [expr_to_dict(x) for x in _canonical_expr_list(o.posts)],
                "impl": expr_to_dict(o.impl),
                "native": o.native,
                "doc": o.doc,
            }
            for o in sorted(p.ops, key=lambda x: x.name)
        ],
        "flows": [
            {
                "name": f.name,
                "params": f.params,
                "ret": f.ret,
                "effects": sorted(f.effects),
                "doc": f.doc,
                "summary": f.summary,
                "body": [
                    {"kind": s.kind, "name": s.name, "expr": expr_to_dict(s.expr)}
                    for s in f.body
                ],
            }
            for f in sorted(p.flows, key=lambda x: x.name)
        ],
    }
    # Omit empty automation lists so programs without agent/task keep their hash.
    if p.agents:
        data["agents"] = [_agent_to_dict(agent) for agent in sorted(p.agents, key=lambda item: item.name)]
    if p.gates:
        data["gates"] = [_gate_to_dict(gate) for gate in sorted(p.gates, key=lambda item: item.name)]
    if p.policies:
        data["policies"] = [_policy_to_dict(policy) for policy in p.policies]
    if p.tasks:
        data["tasks"] = [_task_to_dict(task) for task in sorted(p.tasks, key=lambda item: item.name)]
    return data


def _agent_to_dict(agent: AgentDef) -> dict:
    return {
        "name": agent.name,
        "doc": agent.doc,
        "allow": sorted(agent.allow),
        "budget_run": agent.budget_run,
        "budget_period": agent.budget_period,
        "limit_net": agent.limit_net,
        "limit_emit": agent.limit_emit,
        "audit": agent.audit,
        "timezone": PERIOD_TIMEZONE,
    }


def _gate_to_dict(gate: GateDef) -> dict:
    return {
        "name": gate.name,
        "doc": gate.doc,
        "approver": gate.approver,
        "before": sorted(gate.before),
        "on_approved": gate.on_approved,
    }


def _policy_to_dict(policy: PolicyDef) -> dict:
    return {
        "external": sorted(policy.external),
        "payment": sorted(policy.payment),
        "contract": sorted(policy.contract),
    }


def _task_to_dict(task: TaskDef) -> dict:
    item: dict[str, Any] = {
        "name": task.name,
        "params": task.params,
        "ret": task.ret,
        "doc": task.doc,
        "agent": task.agent,
        "trigger": _trigger_to_dict(task),
        "effects": sorted(task.effects),
        "gates": sorted(task.gates),
        "key": task.key,
        "retry": task.retry,
        "escalate": task.escalate,
        "body": [
            {"kind": stmt.kind, "name": stmt.name, "expr": expr_to_dict(stmt.expr)}
            for stmt in task.body
        ],
    }
    if task.budget_run is not None and task.budget_period is not None:
        item["budget_run"] = task.budget_run
        item["budget_period"] = task.budget_period
    return item


def _trigger_to_dict(task: TaskDef) -> dict:
    if task.trigger_kind == "schedule":
        return {"kind": "schedule", "cron": task.trigger_value, "timezone": PERIOD_TIMEZONE}
    if task.trigger_kind == "record":
        # New rows only. Updates do not start the task.
        return {"kind": "record", "name": task.trigger_value, "on": "insert"}
    if task.trigger_kind == "webhook":
        # Receiver name only. Signatures are not part of this check.
        return {"kind": "webhook", "name": task.trigger_value}
    return {"kind": task.trigger_kind, "value": task.trigger_value}


def canonical_json(p: Program) -> str:
    return json.dumps(program_to_dict(p), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ast_digest(p: Program) -> str:
    """Full SHA-256 of the canonical AST. This is the audit identity."""
    return hashlib.sha256(canonical_json(p).encode("utf-8")).hexdigest()


def ast_hash(p: Program) -> str:
    """12-hex display prefix of ast_digest. Existing callers keep this shape."""
    return ast_digest(p)[:12]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(prog="prout")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="parse and check a PROUT file")
    c.add_argument("file")
    c.add_argument("--allow-net", action="append", default=None)
    c.add_argument("--deny-net", action="append", default=None)
    c.add_argument("--json", action="store_true")
    a = sub.add_parser("ast", help="print the canonical AST")
    a.add_argument("file")
    imported = sub.add_parser("import", help="convert a tool list into PROUT op declarations")
    imported_kind = imported.add_subparsers(dest="kind", required=True)
    mcp = imported_kind.add_parser("mcp", help="convert an MCP tools/list JSON document")
    mcp.add_argument("file")
    openai = imported_kind.add_parser("openai", help="convert OpenAI function-calling tool JSON")
    openai.add_argument("file")
    args = ap.parse_args()

    if args.cmd == "import":
        _import_tools(args.kind, args.file)
        return

    text = open(args.file, encoding="utf-8").read()
    try:
        program = Parser(text).parse()
    except ProutError as ex:
        print(f"[ERROR] {ex.message} (line {ex.line})", file=sys.stderr)
        sys.exit(2)

    if args.cmd == "ast":
        print(json.dumps(program_to_dict(program), ensure_ascii=False, indent=2))
        return

    checker = Checker(program, allow_net=args.allow_net, deny_net=args.deny_net)
    diags = checker.check()
    plan = task_plan(program)
    if args.json:
        payload: dict[str, Any] = {
            "hash": ast_hash(program),
            "diagnostics": [d.as_dict() for d in diags],
        }
        if plan:
            payload["audit_id"] = ast_digest(program)
            payload["plan"] = plan
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"PROUT check: {args.file}")
        print(f"AST hash: {ast_hash(program)}")
        if plan:
            print()
            for line in plan:
                print(line)
        if not diags:
            print("OK — no issues")
        for d in diags:
            print(d.render())
        errors = sum(1 for d in diags if d.severity == "error")
        warnings = sum(1 for d in diags if d.severity == "warning")
        print(f"\n{errors} error(s), {warnings} warning(s)")
    sys.exit(1 if any(d.severity == "error" for d in diags) else 0)


def _import_tools(kind: str, path: str) -> None:
    """Print converted op declarations. The checker is not involved."""
    from prout_import import ToolImportError, render_file

    try:
        sys.stdout.write(render_file(kind, path))
    except (ToolImportError, OSError) as ex:
        print(f"[ERROR] {ex}", file=sys.stderr)
        sys.exit(2)


def task_plan(program: Program) -> list[str]:
    """Readable summary of each task. Empty when the program declares none."""
    if not program.tasks:
        return []
    agents = {agent.name: agent for agent in program.agents}
    gates = {gate.name: gate for gate in program.gates}
    lines = [f"{len(program.tasks)} task(s):"]
    for task in program.tasks:
        title = task.doc or task.name
        lines.append(f"  {task.name} — {title}")
        lines.append(f"    agent: {task.agent or '?'}")
        lines.append(f"    trigger: {_format_trigger(task)}")
        if task.effects:
            lines.append("    effects: " + ", ".join(format_effect(effect) for effect in task.effects))
        else:
            lines.append("    effects: (none)")
        if task.gates:
            for name in task.gates:
                gate = gates.get(name)
                if gate is None:
                    lines.append(f"    gate: {name} (not declared)")
                    continue
                before = ", ".join(format_effect(effect) for effect in gate.before) or "(none)"
                hook = f", on_approved {gate.on_approved}" if gate.on_approved else ""
                lines.append(f"    gate: {name}, approver {gate.approver or '?'}, before {before}{hook}")
        else:
            lines.append("    gate: none")
        lines.append(f"    retry: {task.retry}, escalate {task.escalate or '?'}")
        lines.append(f"    key: {task.key or '?'}")
        if task.budget_run is not None and task.budget_period is not None:
            lines.append(f"    budget: run {task.budget_run} / period {task.budget_period} yen")
        else:
            agent = agents.get(task.agent)
            if agent is not None:
                lines.append(f"    budget: agent run {agent.budget_run} / period {agent.budget_period} yen")
        agent = agents.get(task.agent)
        if agent is not None:
            lines.append(f"    limits: net {agent.limit_net}/hour, emit {agent.limit_emit}/hour")
    lines.append(f"audit id: {ast_digest(program)} (display {ast_hash(program)})")
    return lines


def _format_trigger(task: TaskDef) -> str:
    if task.trigger_kind == "schedule":
        return f'schedule "{task.trigger_value}" ({PERIOD_TIMEZONE})'
    if task.trigger_kind == "record":
        return f"record {task.trigger_value} on insert"
    if task.trigger_kind == "webhook":
        return f'webhook "{task.trigger_value}"'
    return "(none)"


if __name__ == "__main__":
    main()
