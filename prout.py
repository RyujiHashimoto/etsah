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
    context: Optional[dict] = None

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
        if self.context:
            d["context"] = self.context
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

class TypeRef(str):
    """A base type name plus an unordered set of information-flow labels.

    ``TypeRef`` is a ``str`` so existing base-type comparisons keep working.
    Labels are intentionally not part of ``==``; compare ``.labels`` when the
    information-flow meaning matters.
    """

    labels: frozenset[str]

    def __new__(cls, base: str, labels: "frozenset[str] | set[str] | None" = None):
        obj = super().__new__(cls, base)
        obj.labels = frozenset(labels or ())
        return obj

    def with_labels(self, labels: "frozenset[str] | set[str]") -> "TypeRef":
        return TypeRef(str(self), labels)

    def __repr__(self) -> str:
        return f"TypeRef({str(self)!r}, {sorted(self.labels)!r})"


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
    fields: dict[str, TypeRef] = field(default_factory=dict)
    opaque: bool = False
    line: int = 0
    labels: frozenset[str] = frozenset()


@dataclass
class LabelDef:
    name: str
    doc: str = ""
    line: int = 0


@dataclass
class Sends:
    args: list[str] = field(default_factory=list)
    effect: tuple[str, str] = ("", "")
    line: int = 0


@dataclass
class SinkRule:
    kind: str
    target: str
    mode: str
    labels: list[str] = field(default_factory=list)
    line: int = 0


@dataclass
class DeclassifyRule:
    label: str
    scope: str
    role: str
    line: int = 0


@dataclass
class PolicyDef:
    sinks: list[SinkRule] = field(default_factory=list)
    declassify: list[DeclassifyRule] = field(default_factory=list)
    line: int = 0


@dataclass
class OpDef:
    name: str
    params: list[tuple[str, TypeRef]]
    ret: TypeRef
    line: int = 0
    doc: str = ""
    effects: list[tuple[str, str]] = field(default_factory=list)
    pres: list[Expr] = field(default_factory=list)
    posts: list[Expr] = field(default_factory=list)
    impl: Optional[Expr] = None
    native: bool = False
    sends: list[Sends] = field(default_factory=list)


@dataclass
class FlowDef:
    name: str
    params: list[tuple[str, TypeRef]]
    ret: TypeRef
    line: int = 0
    doc: str = ""
    summary: str = ""
    effects: list[tuple[str, str]] = field(default_factory=list)
    body: list[Stmt] = field(default_factory=list)


@dataclass
class Program:
    types: list[TypeDef] = field(default_factory=list)
    ops: list[OpDef] = field(default_factory=list)
    flows: list[FlowDef] = field(default_factory=list)
    source: str = ""
    labels: list[LabelDef] = field(default_factory=list)
    policies: list[PolicyDef] = field(default_factory=list)
    label_errors: list[tuple[str, int]] = field(default_factory=list)


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
            if name == "declassify" and self.peek()[1] == "(":
                self.advance()
                args = [self.parse_or()]
                while self.match(","):
                    args.append(self.parse_or())
                self.expect(")")
                if len(args) != 3:
                    raise ProutError("declassify expects 3 arguments", line)
                return Expr("declassify", line=line, name="declassify", args=args)
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


EFFECT_KINDS = {"read", "write", "net", "spend", "emit"}
LABEL_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TYPE_RE = r"[A-Za-z_]\w*(?:<[^>]+>)?"


def parse_type_ref(s: str, line: int, issues: list[tuple[str, int]]) -> TypeRef:
    """Parse ``Base @label @label`` into a TypeRef.

    Structural label problems are collected as ``E-LABEL`` diagnostics rather
    than parse errors, so the rest of the program can still be checked.
    """
    s = s.strip()
    if not s:
        raise ProutError("empty type", line)

    depth = 0
    cut = -1
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "@" and depth == 0:
            cut = i
            break

    if cut < 0:
        if "@" in s:
            issues.append((f"label inside a type argument is not supported in v0.1: {s!r}", line))
            s = re.sub(r"\s*@\s*[A-Za-z_]\w*", "", s)
        base = s.strip()
        if not re.fullmatch(TYPE_RE, base):
            raise ProutError(f"bad type {s!r}", line)
        return TypeRef(base)

    base = s[:cut].strip()
    rest = s[cut:]
    if not re.fullmatch(TYPE_RE, base):
        raise ProutError(f"bad type {base!r}", line)

    labels: list[str] = []
    pos = 0
    for m in re.finditer(r"@\s*([A-Za-z_]\w*)", rest):
        if rest[pos:m.start()].strip():
            raise ProutError(f"bad label list {rest!r}", line)
        pos = m.end()
        name = m.group(1)
        if name == "public":
            issues.append(("public is implicit and cannot be written as a label", line))
        elif not LABEL_RE.fullmatch(name):
            issues.append((f"label {name!r} must be lowercase", line))
        elif name in EFFECT_KINDS:
            issues.append((f"label {name!r} conflicts with an effect kind", line))
        elif name in labels:
            issues.append((f"duplicate label {name!r}", line))
        else:
            labels.append(name)
    if rest[pos:].strip():
        raise ProutError(f"bad label list {rest!r}", line)
    return TypeRef(base, labels)


def parse_params(s: str, line: int, issues: list[tuple[str, int]]) -> list[tuple[str, TypeRef]]:
    out: list[tuple[str, TypeRef]] = []
    for p in split_top_level(s):
        if ":" not in p:
            raise ProutError(f"bad parameter {p!r}; expected name: Type", line)
        n, t = p.split(":", 1)
        out.append((n.strip(), parse_type_ref(t, line, issues)))
    return out


def parse_effects(s: str, line: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in split_top_level(s):
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*\(\s*([^)]*)\s*\)", p.strip())
        if not m:
            raise ProutError(f"bad effect {p!r}; expected kind(target)", line)
        out.append((m.group(1), m.group(2).strip()))
    return out


class Parser:
    def __init__(self, text: str):
        self.source_text = text
        self.lines = preprocess(text)
        self.i = 0
        self.issues: list[tuple[str, int]] = []

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
        labels: list[LabelDef] = []
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
            elif line.text.startswith("label "):
                labels.append(self.parse_label())
            elif line.text == "policy":
                policies.append(self.parse_policy())
            else:
                raise ProutError(f"unknown declaration {line.text!r}", line.no)
        return Program(types, ops, flows, self.source_text, labels, policies, self.issues)

    def parse_label(self) -> LabelDef:
        line = self.advance()
        m = re.fullmatch(r"label\s+([A-Za-z_]\w*)(?:\s+(.+))?", line.text)
        if not m:
            raise ProutError(f"bad label declaration {line.text!r}", line.no)
        doc = self.parse_string(m.group(2), line.no) if m.group(2) else ""
        return LabelDef(m.group(1), doc, line.no)

    def parse_type(self) -> TypeDef:
        line = self.advance()
        m = re.fullmatch(r"type\s+([A-Za-z_]\w*)((?:\s*@\s*[A-Za-z_]\w*)*)\s*\{", line.text)
        if m:
            name = m.group(1)
            labels = parse_type_ref(name + m.group(2), line.no, self.issues).labels
            fields: dict[str, TypeRef] = {}
            while True:
                nxt = self.peek()
                if nxt is None:
                    raise ProutError(f"unterminated type {name}", line.no)
                if nxt.indent == 0 and nxt.text == "}":
                    self.advance()
                    break
                if nxt.indent != 2:
                    raise ProutError("type fields must be indented by 2 spaces", nxt.no)
                fm = re.fullmatch(r"([A-Za-z_]\w*)\s*:\s*(.+)", nxt.text)
                if not fm:
                    raise ProutError(f"bad field declaration {nxt.text!r}", nxt.no)
                fields[fm.group(1)] = parse_type_ref(fm.group(2), nxt.no, self.issues)
                self.advance()
            return TypeDef(name, fields, False, line.no, labels)
        m = re.fullmatch(r"type\s+([A-Za-z_]\w*)((?:\s*@\s*[A-Za-z_]\w*)*)", line.text)
        if m:
            ref = parse_type_ref(m.group(1) + m.group(2), line.no, self.issues)
            return TypeDef(m.group(1), {}, True, line.no, ref.labels)
        raise ProutError(f"bad type declaration {line.text!r}", line.no)

    def parse_op(self) -> OpDef:
        line = self.advance()
        m = re.fullmatch(
            r"op\s+([A-Za-z_][\w.]*)\s*\((.*?)\)\s*->\s*(.+)",
            line.text,
        )
        if not m:
            raise ProutError(f"bad op header {line.text!r}", line.no)
        name, params_s, ret = m.group(1), m.group(2), m.group(3)
        op = OpDef(
            name,
            parse_params(params_s, line.no, self.issues),
            parse_type_ref(ret, line.no, self.issues),
            line.no,
        )
        while self.peek() and self.peek().indent == 2:
            prop = self.advance()
            text = prop.text
            if text.startswith("doc "):
                op.doc = self.parse_string(text[4:], prop.no)
            elif text.startswith("eff "):
                op.effects = parse_effects(text[4:], prop.no)
            elif text.startswith("sends "):
                op.sends.append(self.parse_sends(text[6:], prop.no))
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
            r"flow\s+([A-Za-z_]\w*)\s*\((.*?)\)\s*->\s*(.+)",
            line.text,
        )
        if not m:
            raise ProutError(f"bad flow header {line.text!r}", line.no)
        name, params_s, ret = m.group(1), m.group(2), m.group(3)
        flow = FlowDef(
            name,
            parse_params(params_s, line.no, self.issues),
            parse_type_ref(ret, line.no, self.issues),
            line.no,
        )
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

    def parse_sends(self, s: str, line: int) -> Sends:
        if "->" not in s:
            raise ProutError(f"bad sends clause {s!r}; expected args -> effect", line)
        left, right = s.split("->", 1)
        left = left.strip()
        right = right.strip()
        if left == "none":
            args: list[str] = []
        else:
            args = [a.strip() for a in split_top_level(left)]
            if not args or not all(re.fullmatch(r"[A-Za-z_]\w*", a) for a in args):
                raise ProutError(f"bad sends arguments {left!r}", line)
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*\(\s*([^)]*)\s*\)", right)
        if not m:
            raise ProutError(f"bad sends effect {right!r}", line)
        return Sends(args, (m.group(1), m.group(2).strip()), line)

    def parse_policy(self) -> PolicyDef:
        line = self.advance()
        policy = PolicyDef(line=line.no)
        while self.peek() and self.peek().indent == 2:
            rule = self.advance()
            text = rule.text
            m = re.fullmatch(
                r"sink\s+([A-Za-z_]\w*)\s*\(\s*([^)]*)\s*\)\s+(accepts|rejects)\s+(.+)",
                text,
            )
            if m:
                labels = [x.strip() for x in split_top_level(m.group(4))]
                policy.sinks.append(
                    SinkRule(m.group(1), m.group(2).strip(), m.group(3), labels, rule.no)
                )
                continue
            m = re.fullmatch(
                r"declassify\s+([A-Za-z_]\w*)\s+in\s+([A-Za-z_]\w*)\s+require\s+role\s+([A-Za-z_]\w*)",
                text,
            )
            if m:
                policy.declassify.append(
                    DeclassifyRule(m.group(1), m.group(2), m.group(3), rule.no)
                )
                continue
            raise ProutError(f"bad policy rule {text!r}", rule.no)
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
        self.labels = {l.name: l for l in program.labels}
        self.policy = program.policies[0] if program.policies else PolicyDef()
        self.scope = ""
        self.in_contract = False
        self.has_labels = bool(program.labels or program.policies or program.label_errors) or self._has_annotations()
        self._type_cache: dict[int, TypeRef] = {}
        self._declass_cache: dict[int, TypeRef] = {}
        self._seen: set[tuple[str, int, str]] = set()

    def _has_annotations(self) -> bool:
        for t in self.program.types:
            if t.labels or any(f.labels for f in t.fields.values()):
                return True
        for o in self.program.ops:
            if o.ret.labels or any(t.labels for _, t in o.params) or o.sends:
                return True
        for f in self.program.flows:
            if f.ret.labels or any(t.labels for _, t in f.params):
                return True
        return False

    def error(self, code: str, message: str, line: int = 0, fix: Optional[str] = None, detail: Optional[str] = None) -> None:
        self.diags.append(Diagnostic(code, "error", message, line, fix, detail))

    def warn(self, code: str, message: str, line: int = 0, fix: Optional[str] = None, detail: Optional[str] = None) -> None:
        self.diags.append(Diagnostic(code, "warning", message, line, fix, detail))

    def check(self) -> list[Diagnostic]:
        self.check_label_declarations()
        self.check_duplicates()
        for t in self.program.types:
            self.check_type(t)
        for op in self.program.ops:
            self.check_op(op)
        for flow in self.program.flows:
            self.check_flow(flow)
        for op in self.program.ops:
            self.check_policy(op)
        if self.has_labels:
            self.check_policy_rules()
            for op in self.program.ops:
                self.check_sends(op)
        return self.diags

    def _once(self, code: str, line: int, message: str) -> bool:
        key = (code, line, message)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def iflow_error(
        self,
        code: str,
        message: str,
        line: int = 0,
        fix: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> None:
        if self._once(code, line, message):
            self.diags.append(Diagnostic(code, "error", message, line, fix, None, context))

    def iflow_warn(
        self,
        code: str,
        message: str,
        line: int = 0,
        fix: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> None:
        if self._once(code, line, message):
            self.diags.append(Diagnostic(code, "warning", message, line, fix, None, context))

    def check_label_declarations(self) -> None:
        for message, line in self.program.label_errors:
            self.iflow_error("E-LABEL", message, line)
        seen: dict[str, int] = {}
        for label in self.program.labels:
            if label.name == "public":
                self.iflow_error("E-LABEL", "public is reserved and cannot be declared as a label", label.line)
            elif not LABEL_RE.fullmatch(label.name):
                self.iflow_error("E-LABEL", f"label {label.name!r} must match [a-z][a-z0-9_]*", label.line)
            elif label.name in EFFECT_KINDS:
                self.iflow_error("E-LABEL", f"label {label.name!r} conflicts with an effect kind", label.line)
            if label.name in seen:
                self.iflow_error("E-LABEL", f"duplicate label {label.name!r}", label.line)
            seen[label.name] = label.line

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
        if t.labels:
            if t.opaque:
                self.check_ref_labels(TypeRef(t.name, t.labels), t.line, f"type {t.name}")
            else:
                self.iflow_error(
                    "E-LABEL",
                    f"record type {t.name!r} cannot carry labels; label its fields",
                    t.line,
                )
        for fname, ftype in t.fields.items():
            self.check_type_exists(ftype, t.line, f"field {t.name}.{fname}")
            self.check_ref_labels(ftype, t.line, f"field {t.name}.{fname}")

    def check_type_exists(self, tname: str, line: int, where: str = "") -> None:
        base = re.sub(r"<.*>", "", str(tname)).strip()
        if base not in self.types and base not in ("Unknown",):
            self.error("E-TYPE", f"unknown type {tname!r}" + (f" in {where}" if where else ""), line)

    def check_ref_labels(self, ref: TypeRef, line: int, where: str = "") -> None:
        if not self.has_labels:
            return
        for label in sorted(ref.labels):
            if label not in self.labels:
                self.iflow_error(
                    "E-LABEL",
                    f"unknown label {label!r}" + (f" in {where}" if where else ""),
                    line,
                )
        base = str(ref)
        t = self.types.get(base)
        if t is not None and not t.opaque and ref.labels:
            self.iflow_error(
                "E-LABEL",
                f"record type {base!r} cannot carry labels; label its fields",
                line,
            )

    def type_labels(self, base: str, seen: frozenset[str] = frozenset()) -> frozenset[str]:
        if base in seen:
            return frozenset()
        t = self.types.get(base)
        if t is None:
            return frozenset()
        if t.opaque:
            return t.labels
        out: set[str] = set(t.labels)
        seen = seen | {base}
        for field_type in t.fields.values():
            out |= set(field_type.labels)
            out |= self.type_labels(str(field_type), seen)
        return frozenset(out)

    def effective(self, ref: TypeRef) -> frozenset[str]:
        labels = ref.labels | self.type_labels(str(ref))
        return frozenset(label for label in labels if label in self.labels)

    def type_of(self, e: Optional[Expr], env: dict[str, TypeRef]) -> TypeRef:
        if e is None:
            return TypeRef("Unknown")
        key = id(e)
        if key in self._type_cache:
            return self._type_cache[key]
        result = self._type_of(e, env)
        self._type_cache[key] = result
        return result

    def _type_of(self, e: Expr, env: dict[str, TypeRef]) -> TypeRef:
        if e.kind == "lit":
            base = {"int": "Int", "str": "String", "bool": "Bool", "date": "Date", "dec": "Decimal"}.get(e.name, "Unknown")
            return TypeRef(base)
        if e.kind == "var":
            if e.name in env:
                ref = env[e.name]
                return TypeRef(str(ref), self.effective(ref))
            self.error("E-UNDEF", f"unknown variable {e.name!r}", e.line)
            return TypeRef("Unknown")
        if e.kind == "field":
            bt = self.type_of(e.base, env)
            t = self.types.get(str(bt))
            if t is None or e.name not in t.fields:
                self.error("E-FIELD", f"type {bt} has no field {e.name!r}", e.line)
                return TypeRef("Unknown")
            ref = t.fields[e.name]
            return TypeRef(str(ref), self.effective(ref))
        if e.kind == "call":
            op = self.ops.get(e.name)
            if op is None:
                self.error("E-UNDEF", f"unknown operation {e.name!r}", e.line)
                return TypeRef("Unknown")
            if len(e.args) != len(op.params):
                self.error("E-ARITY", f"{e.name} expects {len(op.params)} argument(s), got {len(e.args)}", e.line)
                return TypeRef(str(op.ret), self.effective(op.ret))
            for (pname, ptype), arg in zip(op.params, e.args):
                at = self.type_of(arg, env)
                if at != "Unknown" and not self.compatible(ptype, at):
                    self.error("E-TYPE", f"argument {pname!r} of {e.name} expects {ptype}, got {at}", arg.line or e.line)
            return TypeRef(str(op.ret), self.effective(op.ret))
        if e.kind == "declassify":
            return self.declassify_type(e, env)
        if e.kind == "unary":
            lt = self.type_of(e.left, env)
            if e.name == "!":
                if lt not in ("Bool", "Unknown"):
                    self.error("E-TYPE", f"operator ! expects a Bool operand, got {lt}", e.line)
                return TypeRef("Bool", lt.labels)
            return TypeRef("Unknown")
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
                return TypeRef("Bool", lt.labels | rt.labels)
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
                return TypeRef("Bool", lt.labels | rt.labels)
            lt = self.type_of(e.left, env)
            rt = self.type_of(e.right, env)
            labels = lt.labels | rt.labels
            if op in ("&&", "||"):
                if lt not in ("Bool", "Unknown") or rt not in ("Bool", "Unknown"):
                    self.error("E-TYPE", f"operator {op} expects Bool operands, got {lt} and {rt}", e.line)
                return TypeRef("Bool", labels)
            if op == "+":
                if lt == "Unknown" or rt == "Unknown":
                    return TypeRef("Unknown", labels)
                if lt == "Date" and rt == "Date":
                    self.error("E-TYPE", "cannot add Date and Date", e.line)
                    return TypeRef("Unknown", labels)
                if (lt == "Date" and rt == "Int") or (lt == "Int" and rt == "Date"):
                    return TypeRef("Date", labels)
                if lt == rt:
                    return TypeRef(str(lt), labels)
                if {lt, rt} == {"Money", "Int"}:
                    return TypeRef("Money", labels)
                if {lt, rt} <= {"Int", "Decimal"}:
                    return TypeRef("Decimal", labels)
                self.error("E-TYPE", f"cannot add {lt} and {rt}", e.line)
                return TypeRef("Unknown", labels)
            if op == "-":
                if lt == "Unknown" or rt == "Unknown":
                    return TypeRef("Unknown", labels)
                if lt == "Date" and rt == "Date":
                    return TypeRef("Int", labels)
                if lt == "Date" and rt == "Int":
                    return TypeRef("Date", labels)
                if lt == "Money" and rt in ("Money", "Int"):
                    return TypeRef("Money", labels)
                if lt == "Int" and rt == "Int":
                    return TypeRef("Int", labels)
                if {lt, rt} <= {"Int", "Decimal"}:
                    return TypeRef("Decimal", labels)
                self.error("E-TYPE", f"cannot subtract {rt} from {lt}", e.line)
                return TypeRef("Unknown", labels)
            if op == "*":
                if lt == "Unknown" or rt == "Unknown":
                    return TypeRef("Unknown", labels)
                if lt == "Int" and rt == "Int":
                    return TypeRef("Int", labels)
                if {lt, rt} <= {"Int", "Decimal"}:
                    return TypeRef("Decimal", labels)
                if {lt, rt} == {"Money", "Int"}:
                    return TypeRef("Money", labels)
                self.error("E-TYPE", f"cannot multiply {lt} and {rt}", e.line)
                return TypeRef("Unknown", labels)
            if op == "/":
                if lt == "Unknown" or rt == "Unknown":
                    return TypeRef("Unknown", labels)
                if lt == "Int" and rt == "Int":
                    return TypeRef("Int", labels)
                if {lt, rt} <= {"Int", "Decimal"}:
                    return TypeRef("Decimal", labels)
                if lt == "Money" and rt == "Int":
                    return TypeRef("Money", labels)
                self.error("E-TYPE", f"cannot divide {lt} by {rt}", e.line)
                return TypeRef("Unknown", labels)
        return TypeRef("Unknown")

    def declassify_type(self, e: Expr, env: dict[str, TypeRef]) -> TypeRef:
        key = id(e)
        if key in self._declass_cache:
            return self._declass_cache[key]
        if len(e.args) != 3:
            self.iflow_error("E-DECLASS", "declassify expects 3 arguments", e.line)
            result = TypeRef("Unknown")
            self._declass_cache[key] = result
            return result

        val = self.type_of(e.args[0], env)
        label_expr = e.args[1]
        label = label_expr.name if label_expr.kind == "var" else ""
        reason_expr = e.args[2]
        result = TypeRef(str(val), val.labels)

        def fail(message: str, fix: Optional[str] = None, context: Optional[dict] = None) -> TypeRef:
            self.iflow_error("E-DECLASS", message, e.line, fix, context)
            self._declass_cache[key] = result
            return result

        if not label or (label != "public" and label not in self.labels):
            return fail(
                "declassify target must be a declared label or public",
                fix="declare the label, or use public to remove every label",
            )

        if reason_expr.kind != "lit" or reason_expr.name != "str":
            return fail(
                "declassify reason must be a string literal",
                fix='pass a quoted reason, for example "support ticket"',
                context={"label": label, "scope": self.scope, "reason_kind": "not_string"},
            )
        reason = str(reason_expr.value)
        if "\n" in reason or "\r" in reason:
            return fail(
                "declassify reason must not contain a newline",
                context={"label": label, "scope": self.scope, "reason_kind": "newline"},
            )
        if not reason.strip():
            return fail(
                "declassify reason must not be empty",
                context={"label": label, "scope": self.scope, "reason_kind": "empty"},
            )
        if len(reason.strip()) > 200:
            return fail(
                "declassify reason must be 200 characters or fewer",
                context={"label": label, "scope": self.scope, "reason_kind": "too_long"},
            )

        if label == "public":
            if not val.labels:
                return fail("declassify public requires a value with at least one label")
        elif label not in val.labels:
            return fail(f"declassify target label {label!r} is not present on the value")

        if not any(rule.label == label and rule.scope == self.scope for rule in self.policy.declassify):
            return fail(
                f"declassify {label} is not allowed in {self.scope or 'this scope'}",
                fix=f"add: declassify {label} in {self.scope} require role <role>",
            )

        base_type = self.types.get(str(val))
        if base_type is not None and not base_type.opaque:
            return fail("record values cannot be declassified as a whole; declassify each field")

        if self.in_contract:
            return fail("declassify is not allowed in pre or post contracts")

        if label == "public":
            result = TypeRef(str(val), frozenset())
        else:
            result = TypeRef(str(val), val.labels - {label})
        self.iflow_warn(
            "W-DECLASS",
            f"{label} declassified in {self.scope or 'this scope'}",
            e.line,
            fix=f"review the declassification policy for role {self._role_for(label)}",
        )
        self._declass_cache[key] = result
        return result

    def _role_for(self, label: str) -> str:
        for rule in self.policy.declassify:
            if rule.label == label and rule.scope == self.scope:
                return rule.role
        return "?"

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
        self.scope = op.name
        self._type_cache.clear()
        self._declass_cache.clear()
        env = {n: t for n, t in op.params}
        for n, t in op.params:
            self.check_type_exists(t, op.line, f"parameter {n}")
            self.check_ref_labels(t, op.line, f"parameter {n}")
        self.check_type_exists(op.ret, op.line, "return type")
        self.check_ref_labels(op.ret, op.line, "return type")
        for kind, target in op.effects:
            if kind not in EFFECT_KINDS:
                self.error("E-EFF", f"unknown effect kind {kind!r}", op.line)
            if not target:
                self.error("E-EFF", f"effect {kind} has no target", op.line)
        self.in_contract = True
        for p in op.pres:
            t = self.type_of(p, env)
            if t not in ("Bool", "Unknown"):
                self.error("E-TYPE", f"precondition must be Bool, got {t}", p.line)
            if self.has_labels:
                self.check_contract_labels(p, t, "precondition")
        for p in op.posts:
            env2 = dict(env)
            env2["result"] = op.ret
            t = self.type_of(p, env2)
            if t not in ("Bool", "Unknown"):
                self.error("E-TYPE", f"postcondition must be Bool, got {t}", p.line)
            if self.has_labels:
                self.check_contract_labels(p, t, "postcondition")
        self.in_contract = False
        if op.impl is not None:
            it = self.type_of(op.impl, env)
            if it != "Unknown" and not self.compatible(op.ret, it):
                self.error("E-TYPE", f"impl returns {it}, expected {op.ret}", op.impl.line)
            if self.has_labels:
                self.check_return_labels(it, op.ret, f"operation {op.name}", op.impl.line)
                for c in self.calls_in(op.impl):
                    self.check_call_labels(c, env)
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
        self.scope = flow.name
        self._type_cache.clear()
        self._declass_cache.clear()
        env = {n: t for n, t in flow.params}
        for n, t in flow.params:
            self.check_type_exists(t, flow.line, f"parameter {n}")
            self.check_ref_labels(t, flow.line, f"parameter {n}")
        self.check_type_exists(flow.ret, flow.line, "return type")
        self.check_ref_labels(flow.ret, flow.line, "return type")
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
                    self.check_call_labels(c, env)
            elif stmt.kind == "return":
                seen_return = True
                t = self.type_of(stmt.expr, env)
                if t != "Unknown" and not self.compatible(flow.ret, t):
                    self.error("E-TYPE", f"return expects {flow.ret}, got {t}", stmt.line)
                if self.has_labels:
                    self.check_return_labels(t, flow.ret, f"flow {flow.name}", stmt.line)
                inferred |= self.effects_of_expr(stmt.expr)
                for c in self.calls_in(stmt.expr):
                    self.check_call_contracts(c, env)
                    self.check_call_labels(c, env)
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

    @staticmethod
    def labels_desc(labels: frozenset[str] | set[str]) -> str:
        if not labels:
            return "no labels"
        return ", ".join(sorted(labels))

    @staticmethod
    def type_with_labels(base: str, labels: frozenset[str] | set[str]) -> str:
        return base + "".join(" @" + label for label in sorted(labels))

    def check_contract_labels(self, expr: Expr, actual: TypeRef, clause: str) -> None:
        if not actual.labels:
            return
        self.iflow_error(
            "E-IFLOW-CONTRACT",
            f"{clause} uses {self.labels_desc(actual.labels)} value",
            expr.line,
            fix="use a public field, or remove the label from the contract",
            context={"clause": clause, "actual_labels": sorted(actual.labels)},
        )

    def check_return_labels(self, actual: TypeRef, declared: TypeRef, where: str, line: int) -> None:
        expected = self.effective(declared)
        extra = actual.labels - expected
        if not extra:
            return
        self.iflow_error(
            "E-IFLOW-RET",
            f"{where} returns {self.labels_desc(actual.labels)}, declares {self.labels_desc(expected)}",
            line,
            fix=(
                f"change the return type to {self.type_with_labels(str(declared), actual.labels)}, "
                "or declassify the value"
            ),
            context={"expected_labels": sorted(expected), "actual_labels": sorted(actual.labels)},
        )

    def check_call_labels(self, c: Expr, env: dict[str, TypeRef]) -> None:
        if not self.has_labels:
            return
        op = self.ops.get(c.name)
        if not op or len(c.args) != len(op.params):
            return
        for (pname, pref), arg in zip(op.params, c.args):
            actual = self.type_of(arg, env)
            expected = self.effective(pref)
            extra = actual.labels - expected
            if not extra:
                continue
            self.iflow_error(
                "E-IFLOW-ARG",
                f"argument {pname!r} of {c.name} expects {self.labels_desc(expected)}, "
                f"got {self.labels_desc(actual.labels)}",
                arg.line or c.line,
                fix=(
                    f"declassify the value, or change the parameter to "
                    f"{self.type_with_labels(str(pref), actual.labels)}"
                ),
                context={
                    "argument": pname,
                    "expected_labels": sorted(expected),
                    "actual_labels": sorted(actual.labels),
                },
            )

    def resolve_sink(self, effect: tuple[str, str]) -> tuple[set[str], set[str]]:
        exact = [r for r in self.policy.sinks if (r.kind, r.target) == effect]
        wild = [r for r in self.policy.sinks if r.kind == effect[0] and r.target == "*"]
        exact_accepts = [r for r in exact if r.mode == "accepts"]
        wild_accepts = [r for r in wild if r.mode == "accepts"]
        chosen = exact_accepts if exact_accepts else wild_accepts
        accepts: set[str] = set()
        for rule in chosen:
            accepts |= set(rule.labels)
        rejects: set[str] = set()
        for rule in exact + wild:
            if rule.mode == "rejects":
                rejects |= set(rule.labels)
        return accepts, rejects

    def check_sink(self, effect: tuple[str, str], labels: frozenset[str], line: int) -> None:
        if not labels:
            return
        accepts, rejects = self.resolve_sink(effect)
        rejected = set(labels) & rejects
        if rejected:
            self.iflow_error(
                "E-IFLOW-SINK",
                f"{format_effect(effect)} rejects {self.labels_desc(rejected)}",
                line,
                fix=f"send a declassified value, or remove the reject rule for {format_effect(effect)}",
                context={
                    "sink": format_effect(effect),
                    "accepted_labels": sorted(accepts),
                    "rejected_labels": sorted(rejects),
                    "actual_labels": sorted(labels),
                },
            )
            return
        missing = set(labels) - accepts
        if missing:
            self.iflow_error(
                "E-IFLOW-SINK",
                f"{format_effect(effect)} does not accept {self.labels_desc(missing)}",
                line,
                fix=(
                    f"send a declassified value, or allow "
                    f"{self.labels_desc(missing)} on {format_effect(effect)}"
                ),
                context={
                    "sink": format_effect(effect),
                    "accepted_labels": sorted(accepts),
                    "rejected_labels": sorted(rejects),
                    "actual_labels": sorted(labels),
                },
            )

    def check_sends(self, op: OpDef) -> None:
        params = dict(op.params)
        for sends in op.sends:
            if not op.native:
                self.iflow_error(
                    "E-SINK",
                    f"sends is only allowed on impl native (operation {op.name})",
                    sends.line,
                    fix="remove the sends clause, or change the implementation to native",
                    context={"sends": list(sends.args), "effect": list(sends.effect), "parameter": ""},
                )
            if sends.effect not in op.effects:
                self.iflow_error(
                    "E-SINK",
                    f"sends references undeclared effect {format_effect(sends.effect)}",
                    sends.line,
                    fix=f"declare eff {format_effect(sends.effect)} on the operation",
                    context={"sends": list(sends.args), "effect": list(sends.effect), "parameter": ""},
                )
            elif sends.effect[0] == "read" or sends.effect[0] not in EFFECT_KINDS or not sends.effect[1]:
                self.iflow_error(
                    "E-SINK",
                    f"sends must bind to a concrete non-read sink, got {format_effect(sends.effect)}",
                    sends.line,
                )
            elif sends.effect[1] == "*":
                self.iflow_error(
                    "E-SINK",
                    "sends cannot bind to a wildcard sink",
                    sends.line,
                    fix="name the concrete target, for example net(email)",
                )
            for name in sends.args:
                if name not in params:
                    self.iflow_error(
                        "E-SINK",
                        f"sends references unknown parameter {name!r}",
                        sends.line,
                        fix="use a parameter declared in the op signature",
                        context={"sends": list(sends.args), "effect": list(sends.effect), "parameter": name},
                    )

        if not op.native:
            return

        non_read = [effect for effect in op.effects if effect[0] != "read"]
        labeled_params = [name for name, ref in op.params if self.effective(ref)]
        labeled_ret = bool(self.effective(op.ret))
        has_read = any(kind == "read" for kind, _ in op.effects)
        if non_read and (labeled_params or labeled_ret or has_read):
            for effect in non_read:
                if not any(sends.effect == effect for sends in op.sends):
                    self.iflow_error(
                        "E-SINK-UNBOUND",
                        f"op {op.name} has labeled data but no sends for {format_effect(effect)}",
                        op.line,
                        fix=f"add: sends none -> {format_effect(effect)} if no labeled data reaches it",
                        context={"effect": list(effect), "labeled_parameters": labeled_params},
                    )

        for sends in op.sends:
            for name in sends.args:
                if name in params:
                    self.check_sink(sends.effect, self.effective(params[name]), sends.line)

    def check_policy_rules(self) -> None:
        if len(self.program.policies) > 1:
            for extra in self.program.policies[1:]:
                self.iflow_error(
                    "E-POLICY",
                    "a program may contain only one policy block",
                    extra.line,
                )
        for policy in self.program.policies:
            for rule in policy.sinks:
                if rule.kind not in EFFECT_KINDS or rule.kind == "read":
                    self.iflow_error(
                        "E-POLICY",
                        f"sink rule uses invalid effect kind {rule.kind!r}",
                        rule.line,
                        fix="use write, net, spend, or emit",
                    )
                if not rule.target:
                    self.iflow_error(
                        "E-POLICY",
                        f"sink {rule.kind}() has no target",
                        rule.line,
                        fix="name a target, or use * for a wildcard",
                    )
                for label in rule.labels:
                    if label == "public":
                        self.iflow_error(
                            "E-LABEL",
                            "public is implicit and cannot appear in a sink rule",
                            rule.line,
                        )
                    elif label not in self.labels:
                        self.iflow_error("E-LABEL", f"unknown label {label!r} in policy", rule.line)

            by_effect: dict[tuple[str, str], dict[str, set[str]]] = {}
            for rule in policy.sinks:
                key = (rule.kind, rule.target)
                modes = by_effect.setdefault(key, {"accepts": set(), "rejects": set()})
                modes[rule.mode] |= set(rule.labels)
            for (kind, target), modes in by_effect.items():
                both = modes["accepts"] & modes["rejects"]
                if both:
                    self.iflow_error(
                        "E-POLICY",
                        f"sink {kind}({target}) both accepts and rejects {self.labels_desc(both)}",
                        policy.line,
                    )

            seen_declass: set[tuple[str, str]] = set()
            for rule in policy.declassify:
                if rule.label != "public" and rule.label not in self.labels:
                    self.iflow_error(
                        "E-LABEL",
                        f"unknown label {rule.label!r} in declassify rule",
                        rule.line,
                    )
                if rule.scope not in self.ops and rule.scope not in self.flows:
                    self.iflow_error(
                        "E-POLICY",
                        f"declassify scope {rule.scope!r} is not a declared op or flow",
                        rule.line,
                    )
                key = (rule.label, rule.scope)
                if key in seen_declass:
                    self.iflow_error(
                        "E-POLICY",
                        f"duplicate declassify rule for {rule.label} in {rule.scope}",
                        rule.line,
                    )
                seen_declass.add(key)

    def check_policy(self, op: OpDef) -> None:
        for kind, target in op.effects:
            if kind == "net":
                if target in self.deny_net:
                    self.error("E-POLICY", f"network target {target!r} is denied by policy", op.line)
                elif target not in self.allow_net:
                    self.error("E-POLICY", f"network target {target!r} is not allowed by policy", op.line)


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
    elif e.kind == "declassify":
        d["name"] = "declassify"
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
    d: dict[str, Any] = {}
    if p.labels:
        d["labels"] = [
            {"name": label.name, "doc": label.doc}
            for label in sorted(p.labels, key=lambda x: x.name)
        ]

    types: list[dict] = []
    for t in sorted(p.types, key=lambda x: x.name):
        td: dict[str, Any] = {
            "name": t.name,
            "opaque": t.opaque,
            "fields": {name: str(ref) for name, ref in sorted(t.fields.items())},
        }
        field_labels = {
            name: sorted(ref.labels)
            for name, ref in sorted(t.fields.items())
            if ref.labels
        }
        if field_labels:
            td["field_labels"] = field_labels
        if t.opaque and t.labels:
            td["labels"] = sorted(t.labels)
        types.append(td)
    d["types"] = types

    ops: list[dict] = []
    for o in sorted(p.ops, key=lambda x: x.name):
        od: dict[str, Any] = {
            "name": o.name,
            "params": [[name, str(ref)] for name, ref in o.params],
            "ret": str(o.ret),
            "effects": sorted(o.effects),
            "pre": [expr_to_dict(x) for x in _canonical_expr_list(o.pres)],
            "post": [expr_to_dict(x) for x in _canonical_expr_list(o.posts)],
            "impl": expr_to_dict(o.impl),
            "native": o.native,
            "doc": o.doc,
        }
        param_labels = {name: sorted(ref.labels) for name, ref in o.params if ref.labels}
        if param_labels:
            od["param_labels"] = param_labels
        if o.ret.labels:
            od["ret_labels"] = sorted(o.ret.labels)
        if o.sends:
            od["sends"] = [
                {"args": sorted(set(s.args)), "effect": list(s.effect)}
                for s in sorted(o.sends, key=lambda s: (s.effect, tuple(sorted(set(s.args)))))
            ]
        ops.append(od)
    d["ops"] = ops

    flows: list[dict] = []
    for f in sorted(p.flows, key=lambda x: x.name):
        fd: dict[str, Any] = {
            "name": f.name,
            "params": [[name, str(ref)] for name, ref in f.params],
            "ret": str(f.ret),
            "effects": sorted(f.effects),
            "doc": f.doc,
            "summary": f.summary,
            "body": [
                {"kind": s.kind, "name": s.name, "expr": expr_to_dict(s.expr)}
                for s in f.body
            ],
        }
        param_labels = {name: sorted(ref.labels) for name, ref in f.params if ref.labels}
        if param_labels:
            fd["param_labels"] = param_labels
        if f.ret.labels:
            fd["ret_labels"] = sorted(f.ret.labels)
        flows.append(fd)
    d["flows"] = flows

    if p.policies:
        policy = p.policies[0]
        pd: dict[str, Any] = {}
        if policy.sinks:
            pd["sinks"] = [
                {
                    "effect": [rule.kind, rule.target],
                    "mode": rule.mode,
                    "labels": sorted(set(rule.labels)),
                }
                for rule in sorted(
                    policy.sinks,
                    key=lambda r: (r.kind, r.target, r.mode, tuple(sorted(set(r.labels)))),
                )
            ]
        if policy.declassify:
            pd["declassify"] = [
                {"label": rule.label, "scope": rule.scope, "role": rule.role}
                for rule in sorted(
                    policy.declassify,
                    key=lambda r: (r.scope, r.label, r.role),
                )
            ]
        if pd:
            d["policy"] = pd
    return d


def ast_hash(p: Program) -> str:
    blob = json.dumps(program_to_dict(p), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


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
    args = ap.parse_args()

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
    if args.json:
        print(json.dumps({"hash": ast_hash(program), "diagnostics": [d.as_dict() for d in diags]}, ensure_ascii=False, indent=2))
    else:
        print(f"PROUT check: {args.file}")
        print(f"AST hash: {ast_hash(program)}")
        if not diags:
            print("OK — no issues")
        for d in diags:
            print(d.render())
        errors = sum(1 for d in diags if d.severity == "error")
        warnings = sum(1 for d in diags if d.severity == "warning")
        print(f"\n{errors} error(s), {warnings} warning(s)")
    sys.exit(1 if any(d.severity == "error" for d in diags) else 0)


if __name__ == "__main__":
    main()
