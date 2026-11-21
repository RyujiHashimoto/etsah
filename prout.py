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
from typing import Any, Optional


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
class Program:
    types: list[TypeDef] = field(default_factory=list)
    ops: list[OpDef] = field(default_factory=list)
    flows: list[FlowDef] = field(default_factory=list)
    source: str = ""


# ---------------------------------------------------------------------------
# Expression lexer and parser
# ---------------------------------------------------------------------------

TOKEN_RE = re.compile(
    r"""
    (?P<WS>\s+)
  | (?P<NUMBER>\d+)
  | (?P<STRING>"(?:\\.|[^"\\])*")
  | (?P<IDENT>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<OP><=|>=|==|!=|&&|\|\||->|[()\{\},:+\-*/<>!=])
""",
    re.VERBOSE,
)


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
            return Expr("bin", line=op[2], name="!", left=self.parse_unary())
        if self.peek()[1] == "-":
            op = self.advance()
            return Expr("bin", line=op[2], name="-", left=Expr("lit", line=op[2], name="int", value=0), right=self.parse_unary())
        return self.parse_primary()

    def parse_primary(self) -> Expr:
        kind, value, line = self.peek()
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
            else:
                raise ProutError(f"unknown declaration {line.text!r}", line.no)
        return Program(types, ops, flows, self.source_text)

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
    return "?"


def add_coeffs(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + v
    return out


def neg_coeffs(a: dict[str, int]) -> dict[str, int]:
    return {k: -v for k, v in a.items()}


def scale_coeffs(a: dict[str, int], k: int) -> dict[str, int]:
    return {key: v * k for key, v in a.items()}


def linear(e: Optional[Expr]) -> Optional[tuple[dict[str, int], int]]:
    """Return (coefficients over atomic expressions, constant), or None."""
    if e is None:
        return None
    if e.kind == "lit" and e.name == "int":
        return ({}, int(e.value))
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


def evaluate(e: Optional[Expr]) -> Optional[bool]:
    """Three-valued evaluation: True, False, or None (unknown)."""
    if e is None:
        return None
    if e.kind == "lit":
        if e.name == "bool":
            return bool(e.value)
        return None
    if e.kind == "bin":
        op = e.name
        if op == "!":
            v = evaluate(e.left)
            return None if v is None else not v
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
            return None
        if op in ("<", "<=", ">", ">="):
            l = linear(e.left)
            r = linear(e.right)
            if l is None or r is None:
                return None
            coeffs = add_coeffs(l[0], neg_coeffs(r[0]))
            coeffs = {k: v for k, v in coeffs.items() if v != 0}
            const = l[1] - r[1]
            if coeffs:
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
    if e.kind == "bin":
        return Expr("bin", line=e.line, name=e.name, left=subst(e.left, mapping), right=subst(e.right, mapping))
    return e


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

BUILTIN_TYPES = {"Int", "String", "Bool"}
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
            return {"int": "Int", "str": "String", "bool": "Bool"}.get(e.name, "Unknown")
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
        if e.kind == "bin":
            op = e.name
            if op in ("==", "!=", "<", "<=", ">", ">="):
                self.type_of(e.left, env)
                self.type_of(e.right, env)
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
                if lt == rt:
                    return lt
                if {lt, rt} == {"Money", "Int"}:
                    return "Money"
                self.error("E-TYPE", f"cannot add {lt} and {rt}", e.line)
                return "Unknown"
            if op == "-":
                if lt == "Unknown" or rt == "Unknown":
                    return "Unknown"
                if lt == "Money" and rt in ("Money", "Int"):
                    return "Money"
                if lt == "Int" and rt == "Int":
                    return "Int"
                self.error("E-TYPE", f"cannot subtract {rt} from {lt}", e.line)
                return "Unknown"
            if op == "*":
                if lt == "Unknown" or rt == "Unknown":
                    return "Unknown"
                if lt == "Int" and rt == "Int":
                    return "Int"
                if {lt, rt} == {"Money", "Int"}:
                    return "Money"
                self.error("E-TYPE", f"cannot multiply {lt} and {rt}", e.line)
                return "Unknown"
            if op == "/":
                if lt == "Unknown" or rt == "Unknown":
                    return "Unknown"
                if lt == "Int" and rt == "Int":
                    return "Int"
                if lt == "Money" and rt == "Int":
                    return "Money"
                self.error("E-TYPE", f"cannot divide {lt} by {rt}", e.line)
                return "Unknown"
        return "Unknown"

    @staticmethod
    def compatible(expected: str, actual: str) -> bool:
        if expected == actual:
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
        if op.impl:
            it = self.type_of(op.impl, env)
            if it != "Unknown" and not self.compatible(op.ret, it):
                self.error("E-TYPE", f"impl returns {it}, expected {op.ret}", op.impl.line)
            inferred = self.effects_of_expr(op.impl)
            declared = set(op.effects)
            if inferred and declared != inferred:
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
        for stmt in flow.body:
            if stmt.kind == "let":
                t = self.type_of(stmt.expr, env)
                if stmt.name in env:
                    self.error("E-DUP", f"variable {stmt.name!r} is already defined", stmt.line)
                env[stmt.name] = t
                inferred |= self.effects_of_expr(stmt.expr)
                for c in self.calls_in(stmt.expr):
                    self.check_call_contracts(c, env)
            elif stmt.kind == "return":
                t = self.type_of(stmt.expr, env)
                if t != "Unknown" and not self.compatible(flow.ret, t):
                    self.error("E-TYPE", f"return expects {flow.ret}, got {t}", stmt.line)
                inferred |= self.effects_of_expr(stmt.expr)
                for c in self.calls_in(stmt.expr):
                    self.check_call_contracts(c, env)
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


# ---------------------------------------------------------------------------
# Canonical AST and hash
# ---------------------------------------------------------------------------

def expr_to_dict(e: Optional[Expr]) -> Optional[dict]:
    if e is None:
        return None
    d: dict[str, Any] = {"kind": e.kind}
    if e.kind == "lit":
        d["type"] = e.name
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
    return d


def program_to_dict(p: Program) -> dict:
    return {
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
                "pre": [expr_to_dict(x) for x in o.pres],
                "post": [expr_to_dict(x) for x in o.posts],
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
