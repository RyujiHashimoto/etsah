#!/usr/bin/env python3
"""
PROUT Patterns v0.2 — a growing pattern language prototype.

Patterns are data, not code. The compiler loads every manifest in registry/,
validates app specs against the manifests, and expands each pattern into a
deterministic set of database, API, UI, and test artifacts.

Commands:
    build   validate and expand an app spec
    find    search the registry by intent, tag, name, or slot
    add     validate a manifest and add it to the registry
    index   rebuild registry/index.json for AI discovery
    bench   compare apps with and without a reusable pattern
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
DEFAULT_REGISTRY = ROOT / "registry"


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

@dataclass
class Diagnostic:
    code: str
    message: str
    line: int = 0
    severity: str = "error"

    def render(self) -> str:
        loc = f"line {self.line}" if self.line else "no line"
        return f"[{self.severity.upper()}] {self.code} ({loc}): {self.message}"


# ---------------------------------------------------------------------------
# Source model
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
            raise ValueError(f"tabs are not allowed for indentation (line {no})")
        indent = len(leading)
        if indent % 2 != 0:
            raise ValueError(f"indentation must be a multiple of 2 spaces (line {no})")
        lines.append(Line(indent, s.strip(), no))
    return lines


@dataclass
class Entity:
    name: str
    fields: dict[str, str] = field(default_factory=dict)
    line: int = 0


@dataclass
class PatternUse:
    name: str
    target: str = ""
    slots: dict[str, str] = field(default_factory=dict)
    line: int = 0


@dataclass
class CustomBlock:
    name: str
    why: str = ""
    lines: int = 0
    line: int = 0


@dataclass
class App:
    name: str
    stack: list[str] = field(default_factory=list)
    freedom: str = "guided"
    entities: list[Entity] = field(default_factory=list)
    patterns: list[PatternUse] = field(default_factory=list)
    customs: list[CustomBlock] = field(default_factory=list)
    source: str = ""


def parse_string(s: str, line: int) -> str:
    s = s.strip()
    if not (s.startswith('"') and s.endswith('"')):
        raise ValueError(f"expected a quoted string (line {line})")
    return json.loads(s)


def parse_app(text: str) -> App:
    lines = preprocess(text)
    if not lines:
        raise ValueError("empty app spec")
    first = lines[0]
    m = re.fullmatch(r"app\s+([A-Za-z_]\w*)", first.text)
    if not m or first.indent != 0:
        raise ValueError("the first line must be `app <name>` at column 0")
    app = App(m.group(1), source=text)
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.indent != 2:
            raise ValueError(f"app declarations must be indented by 2 spaces (line {line.no})")
        if line.text.startswith("stack "):
            app.stack = [x.strip() for x in line.text[6:].split(",") if x.strip()]
            i += 1
        elif line.text.startswith("freedom "):
            app.freedom = line.text[8:].strip()
            i += 1
        elif line.text.startswith("entity "):
            entity, i = parse_entity(lines, i)
            app.entities.append(entity)
        elif line.text.startswith("pattern "):
            pattern, i = parse_pattern(lines, i)
            app.patterns.append(pattern)
        elif line.text.startswith("custom "):
            custom, i = parse_custom(lines, i)
            app.customs.append(custom)
        else:
            raise ValueError(f"unknown declaration {line.text!r} (line {line.no})")
    return app


def parse_entity(lines: list[Line], i: int) -> tuple[Entity, int]:
    line = lines[i]
    m = re.fullmatch(r"entity\s+([A-Za-z_]\w*)", line.text)
    if not m:
        raise ValueError(f"bad entity header {line.text!r} (line {line.no})")
    entity = Entity(m.group(1), line=line.no)
    i += 1
    while i < len(lines) and lines[i].indent == 4:
        fm = re.fullmatch(r"([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*(?:<[^>]+>)?)", lines[i].text)
        if not fm:
            raise ValueError(f"bad field {lines[i].text!r} (line {lines[i].no})")
        entity.fields[fm.group(1)] = fm.group(2)
        i += 1
    return entity, i


def parse_pattern(lines: list[Line], i: int) -> tuple[PatternUse, int]:
    line = lines[i]
    m = re.fullmatch(r"pattern\s+([A-Za-z_]\w*)(?:\s+([A-Za-z_]\w*))?", line.text)
    if not m:
        raise ValueError(f"bad pattern header {line.text!r} (line {line.no})")
    pattern = PatternUse(m.group(1), m.group(2) or "", line=line.no)
    i += 1
    while i < len(lines) and lines[i].indent == 4:
        sm = re.fullmatch(r"([A-Za-z_]\w*)\s*:\s*(.+)", lines[i].text)
        if not sm:
            raise ValueError(f"bad slot {lines[i].text!r} (line {lines[i].no})")
        pattern.slots[sm.group(1)] = sm.group(2).strip()
        i += 1
    return pattern, i


def parse_custom(lines: list[Line], i: int) -> tuple[CustomBlock, int]:
    line = lines[i]
    m = re.fullmatch(r"custom\s+([A-Za-z_]\w*)", line.text)
    if not m:
        raise ValueError(f"bad custom header {line.text!r} (line {line.no})")
    custom = CustomBlock(m.group(1), line=line.no)
    i += 1
    while i < len(lines) and lines[i].indent == 4:
        text = lines[i].text
        if text.startswith("why "):
            custom.why = parse_string(text[4:], lines[i].no)
        elif text.startswith("lines "):
            try:
                custom.lines = int(text[6:].strip())
            except ValueError:
                raise ValueError(f"custom lines must be an integer (line {lines[i].no})")
        else:
            raise ValueError(f"unknown custom property {text!r} (line {lines[i].no})")
        i += 1
    return custom, i


# ---------------------------------------------------------------------------
# Pattern manifests
# ---------------------------------------------------------------------------

@dataclass
class PatternManifest:
    name: str
    version: str
    intent: str
    tags: list[str] = field(default_factory=list)
    slots: dict[str, dict] = field(default_factory=dict)
    states: list[str] = field(default_factory=list)
    transitions: list[dict] = field(default_factory=list)
    hooks: dict = field(default_factory=dict)
    effects: list[str] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    requires_fields: dict[str, str] = field(default_factory=dict)

    def to_index(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "intent": self.intent,
            "tags": self.tags,
            "slots": {
                k: {"required": bool(v.get("required")), "desc": v.get("desc", "")}
                for k, v in self.slots.items()
            },
            "states": self.states,
            "effects": self.effects,
            "examples": self.examples,
            "path": f"registry/{self.name}.json",
        }


def manifest_from_dict(data: dict) -> PatternManifest:
    return PatternManifest(
        name=data.get("name", ""),
        version=data.get("version", "0.0.0"),
        intent=data.get("intent", ""),
        tags=data.get("tags", []),
        slots=data.get("slots", {}),
        states=data.get("states", []),
        transitions=data.get("transitions", []),
        hooks=data.get("hooks", {}),
        effects=data.get("effects", []),
        artifacts=data.get("artifacts", []),
        examples=data.get("examples", []),
        provenance=data.get("provenance", {}),
        requires_fields=data.get("requires_fields", {}),
    )


def validate_manifest(m: PatternManifest) -> list[str]:
    errors: list[str] = []
    if not re.fullmatch(r"[a-z][a-z0-9_]*", m.name):
        errors.append("name must be lower_snake_case")
    if not m.intent:
        errors.append("intent is required")
    if not m.artifacts:
        errors.append("at least one artifact is required")
    for slot_name, spec in m.slots.items():
        if "type" not in spec:
            errors.append(f"slot {slot_name!r} is missing a type")
    for a in m.artifacts:
        if "path" not in a:
            errors.append("artifact is missing a path")
        if "lines" not in a:
            errors.append(f"artifact {a.get('path', '?')} is missing a line estimate")
    if m.states:
        known = set(m.states)
        for t in m.transitions:
            if t.get("from") not in known:
                errors.append(f"transition from unknown state {t.get('from')!r}")
            if t.get("to") not in known:
                errors.append(f"transition to unknown state {t.get('to')!r}")
            if not t.get("on"):
                errors.append("transition is missing an event name")
    return errors


def load_registry(path: Path) -> dict[str, PatternManifest]:
    registry: dict[str, PatternManifest] = {}
    for p in sorted(path.glob("*.json")):
        if p.name == "index.json":
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        m = manifest_from_dict(data)
        errors = validate_manifest(m)
        if errors:
            raise ValueError(f"invalid manifest {p.name}: {'; '.join(errors)}")
        registry[m.name] = m
    return registry


def write_index(registry_dir: Path) -> Path:
    registry = load_registry(registry_dir)
    index = {
        "version": 1,
        "patterns": [registry[name].to_index() for name in sorted(registry)],
    }
    out = registry_dir / "index.json"
    out.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(app: App, registry: dict[str, PatternManifest]) -> list[Diagnostic]:
    diags: list[Diagnostic] = []

    def err(code: str, message: str, line: int = 0) -> None:
        diags.append(Diagnostic(code, message, line))

    if not app.stack:
        err("E-STACK", "stack is required; declare at least one target")
    if app.freedom not in ("strict", "guided", "open"):
        err("E-FREEDOM", f"freedom must be strict, guided, or open; got {app.freedom!r}")

    entity_names = {e.name for e in app.entities}
    seen_entities: set[str] = set()
    for e in app.entities:
        if e.name in seen_entities:
            err("E-DUP", f"duplicate entity {e.name!r}", e.line)
        seen_entities.add(e.name)
        if not e.fields:
            err("E-ENTITY", f"entity {e.name!r} has no fields", e.line)

    seen_patterns: set[tuple[str, str]] = set()
    custom_names = {c.name for c in app.customs}
    auth_count = 0
    for p in app.patterns:
        m = registry.get(p.name)
        if not m:
            err("E-PATTERN", f"unknown pattern {p.name!r}; run `proutc find` to discover patterns", p.line)
            continue
        for slot_name, spec in m.slots.items():
            if spec.get("required") and slot_name not in p.slots:
                err("E-SLOT", f"pattern {p.name} requires slot {slot_name!r}", p.line)
        for slot_name in p.slots:
            if slot_name not in m.slots:
                diags.append(
                    Diagnostic(
                        "W-SLOT",
                        f"pattern {p.name} has no slot {slot_name!r}; it will be ignored",
                        p.line,
                        "warning",
                    )
                )
        target = p.slots.get("entity", p.target)
        if "entity" in m.slots:
            if not target:
                err("E-TARGET", f"pattern {p.name} requires an entity", p.line)
            elif target not in entity_names:
                err("E-TARGET", f"entity {target!r} is not declared", p.line)
        entity = next((e for e in app.entities if e.name == target), None)
        if entity:
            for fname in m.requires_fields:
                if fname not in entity.fields:
                    err("E-ENTITY", f"{entity.name} needs a `{fname}` field for {p.name}", p.line)
        hook_spec = m.hooks.get("on_approved")
        hook = p.slots.get("on_approved")
        if hook_spec and hook:
            if hook not in custom_names and hook not in registry:
                err(
                    "E-HOOK",
                    f"on_approved {hook!r} is not a custom block or registered pattern",
                    p.line,
                )
            elif hook in registry and not any(other.name == hook for other in app.patterns):
                err(
                    "E-HOOK",
                    f"on_approved pattern {hook!r} is not applied; add `pattern {hook}`",
                    p.line,
                )
        if p.name == "auth":
            auth_count += 1
            if auth_count > 1:
                err("E-CONFLICT", "auth can only be applied once", p.line)
        if p.name in ("checkout", "refund") and target:
            key = (p.name, target)
            if key in seen_patterns:
                err("E-CONFLICT", f"{p.name} is already applied to {target}", p.line)
            seen_patterns.add(key)

    seen_custom: set[str] = set()
    for c in app.customs:
        if c.name in seen_custom:
            err("E-DUP", f"duplicate custom block {c.name!r}", c.line)
        seen_custom.add(c.name)
        if c.lines <= 0:
            err("E-CUSTOM", f"custom {c.name} needs a positive `lines` estimate", c.line)
        if app.freedom == "strict":
            err("E-FREEDOM", "strict mode does not allow custom blocks", c.line)
        if app.freedom == "guided" and not c.why:
            err("E-CUSTOM", f"custom {c.name} needs a `why` in guided mode", c.line)

    return diags


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------

@dataclass
class Artifact:
    pattern: str
    path: str
    kind: str
    lines: int
    desc: str


def plural(name: str) -> str:
    if name.endswith("y") and len(name) > 1 and name[-2] not in "aeiou":
        return name[:-1] + "ies"
    if name.endswith(("s", "x", "z", "ch", "sh")):
        return name + "es"
    return name + "s"


def entity_for(app: App, p: PatternUse) -> Optional[Entity]:
    target = p.slots.get("entity", p.target)
    return next((e for e in app.entities if e.name == target), None)


def template_context(entity: Optional[Entity]) -> dict:
    name = entity.name if entity else ""
    return {
        "entity": name,
        "Entity": name,
        "entity_lower": name.lower(),
        "plural": plural(name).lower() if name else "",
        "Plural": plural(name) if name else "",
        "fields": len(entity.fields) if entity else 0,
    }


def substitute(template: str, ctx: dict) -> str:
    out = template
    for key, value in ctx.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def expand(app: App, registry: dict[str, PatternManifest]) -> list[Artifact]:
    arts: list[Artifact] = []

    schema_lines = 10 + sum(4 + len(e.fields) * 2 for e in app.entities)
    type_lines = 15 + sum(len(e.fields) * 2 for e in app.entities)
    arts.append(Artifact("shared", "db/schema.sql", "SQL", schema_lines, "エンティティから生成したテーブル定義"))
    arts.append(Artifact("shared", "lib/db.ts", "TS", 25, "Prismaクライアントと接続管理"))
    arts.append(Artifact("shared", "lib/types.ts", "TS", type_lines, "共有型とブランド型"))
    arts.append(Artifact("shared", "README.md", "MD", 30, "セットアップと環境変数の手順"))

    for p in app.patterns:
        m = registry.get(p.name)
        if not m:
            continue
        entity = entity_for(app, p)
        ctx = template_context(entity)
        for t in m.artifacts:
            path = substitute(t.get("path", ""), ctx)
            desc = substitute(t.get("desc", ""), ctx)
            lines = int(t.get("lines", 0)) + int(t.get("per_field", 0)) * ctx["fields"]
            arts.append(Artifact(p.name, path, t.get("kind", "TS"), lines, desc))

    for c in app.customs:
        arts.append(Artifact("custom", f"custom/{c.name}.ts", "TS", c.lines, c.why or "custom logic"))

    return arts


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def spec_lines(source: str) -> int:
    return sum(1 for line in source.splitlines() if line.strip() and not line.strip().startswith("#"))


def render_report(app: App, diags: list[Diagnostic], arts: list[Artifact]) -> str:
    total_lines = sum(a.lines for a in arts)
    spec = spec_lines(app.source)
    ratio = round(total_lines / spec, 1) if spec else 0
    errors = [d for d in diags if d.severity == "error"]
    patterns = len(app.patterns)

    groups: dict[str, list[Artifact]] = {}
    for a in arts:
        groups.setdefault(a.pattern, []).append(a)

    pattern_order = ["shared"] + [p.name for p in app.patterns] + ["custom"]
    pattern_labels = {
        "shared": "共通基盤",
        "auth": "認証",
        "crud": "CRUD",
        "checkout": "決済",
        "refund": "返金",
        "dashboard": "ダッシュボード",
        "search": "検索",
        "notify": "通知",
        "upload": "アップロード",
        "approval": "承認ワークフロー",
        "custom": "カスタム",
    }

    group_html = []
    for key in pattern_order:
        items = groups.get(key)
        if not items:
            continue
        lines = sum(a.lines for a in items)
        rows = "".join(
            f"""
            <div class="artifact">
              <div class="artifact-main">
                <code>{esc(a.path)}</code>
                <div class="artifact-desc">{esc(a.desc)}</div>
              </div>
              <div class="artifact-meta"><span class="kind {esc(a.kind.lower())}">{esc(a.kind)}</span><span class="lines">{a.lines}行</span></div>
            </div>
            """
            for a in items
        )
        group_html.append(
            f"""
            <div class="group">
              <div class="group-head">
                <div><span class="group-name">{esc(pattern_labels.get(key, key))}</span><span class="group-key">{esc(key)}</span></div>
                <span class="group-lines">{lines}行</span>
              </div>
              {rows}
            </div>
            """
        )

    spec_rows = []
    for no, line in enumerate(app.source.splitlines(), 1):
        if not line.strip():
            continue
        spec_rows.append(f'<div class="src-line"><span class="ln">{no}</span><span>{esc(line)}</span></div>')
    spec_html = "".join(spec_rows)

    preview_entity = app.entities[0] if app.entities else None
    preview_fields = [
        name
        for name in (preview_entity.fields if preview_entity else {})
        if name not in ("id", "status")
    ][:4]
    preview_rows = ""
    if preview_entity:
        for index, person in enumerate(("山田 太郎", "佐藤 花子", "鈴木 一郎"), 1):
            cells = "".join(
                f"<td>{esc(person if name in ('applicant', 'name') else sample_value(name, preview_entity.fields[name]))}</td>"
                for name in preview_fields
            )
            preview_rows += (
                f'<tr><td class="mono">#{1043 - index}</td>{cells}'
                f'<td><span class="pill approved">active</span></td></tr>'
            )

    approval = next((p for p in app.patterns if p.name == "approval"), None)
    if approval:
        human_panel = (
            '<p style="font-size:12.5px;color:var(--muted);margin-bottom:12px;">'
            '承認前と承認後を、ボタンで確かめられるデモを生成した。'
            '申請・承認・差し戻しのたびに、状態と承認後の処理が変わる。</p>'
            '<p><a class="demo-link" href="demo.html">承認デモを開く →</a></p>'
        )
    elif preview_entity:
        headers = "".join(f"<th>{esc(field)}</th>" for field in preview_fields)
        human_panel = (
            f'<table><thead><tr><th>ID</th>{headers}<th>状態</th></tr></thead>'
            f'<tbody>{preview_rows}</tbody></table>'
        )
    else:
        human_panel = '<p style="font-size:12.5px;color:var(--muted);">エンティティが定義されていない。</p>'

    freedom_text = {
        "strict": "パターンのみ。最速・最も検証しやすい。",
        "guided": "パターン + custom。customにはwhyが必須。",
        "open": "パターン + 任意コード。自由度最大、検証はPROUTへ。",
    }.get(app.freedom, "")

    css = """
    :root {
      --bg: #f4f1e9; --card: #fffdf8; --ink: #1e2328; --muted: #6d7280;
      --line: #e3ddd0; --green: #2f6f4e; --green-soft: #e7f2ea;
      --amber: #a85a08; --amber-soft: #fbf1e0; --red: #b3261e; --red-soft: #fbe9e7;
      --blue: #2454b8; --blue-soft: #e8eefb; --purple: #6335b8; --purple-soft: #f0eafb;
      --shadow: 0 1px 2px rgba(30,35,40,.04), 0 8px 24px rgba(30,35,40,.05);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; color: var(--ink); line-height: 1.7;
      background: radial-gradient(1200px 500px at 50% -200px, #fbf8f1 0%, rgba(251,248,241,0) 70%), var(--bg);
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", "Yu Gothic", sans-serif;
      -webkit-font-smoothing: antialiased;
    }
    .wrap { max-width: 820px; margin: 0 auto; padding: 30px 20px 70px; }
    .kicker { font-family: ui-monospace, Menlo, monospace; font-size: 11px; letter-spacing: .16em; color: var(--green); margin-bottom: 10px; }
    h1 { font-size: 30px; line-height: 1.3; margin: 0 0 8px; letter-spacing: -.02em; }
    h2 { font-size: 15px; margin: 0 0 14px; }
    p { margin: 0 0 12px; }
    code { font-family: ui-monospace, Menlo, monospace; font-size: .88em; background: #f2eee4; padding: 2px 6px; border-radius: 5px; }
    .sub { color: var(--muted); font-size: 14px; margin-bottom: 16px; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 22px; }
    .chip { font-size: 11px; padding: 4px 9px; border-radius: 999px; background: #fffefa; border: 1px solid var(--line); color: #4b5563; }
    .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 18px; }
    .metric { background: var(--card); border: 1px solid var(--line); border-radius: 13px; padding: 14px 15px; box-shadow: var(--shadow); }
    .metric .value { font-size: 26px; font-weight: 750; letter-spacing: -.03em; line-height: 1.2; }
    .metric .label { font-size: 10.5px; color: var(--muted); letter-spacing: .04em; }
    .metric .accent { color: var(--green); }
    .panel { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 22px; margin-top: 18px; box-shadow: var(--shadow); }
    .panel h2 { display: flex; align-items: center; gap: 8px; }
    .panel h2::before { content: ""; width: 4px; height: 16px; border-radius: 2px; background: var(--green); display: inline-block; }
    .src { background: #20262b; border-radius: 10px; padding: 14px 0; overflow-x: auto; }
    .src-line { display: flex; gap: 14px; font-family: ui-monospace, Menlo, monospace; font-size: 11px; line-height: 1.75; color: #e8e4d9; padding: 0 16px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .src-line .ln { color: #6b7280; text-align: right; min-width: 26px; user-select: none; }
    .group { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; margin-bottom: 10px; background: #fffefa; }
    .group-head { display: flex; align-items: center; justify-content: space-between; padding: 11px 14px; background: #f8f5ee; border-bottom: 1px solid var(--line); }
    .group-name { font-weight: 700; font-size: 13.5px; }
    .group-key { font-family: ui-monospace, Menlo, monospace; font-size: 10px; color: var(--muted); margin-left: 8px; }
    .group-lines { font-family: ui-monospace, Menlo, monospace; font-size: 11px; color: var(--green); }
    .artifact { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 9px 14px; border-bottom: 1px dashed #ece6d9; }
    .artifact:last-child { border-bottom: 0; }
    .artifact code { font-size: 11px; background: transparent; padding: 0; color: #334155; overflow-wrap: anywhere; }
    .artifact-desc { font-size: 11px; color: var(--muted); margin-top: 1px; }
    .artifact-meta { display: flex; align-items: center; gap: 8px; flex: 0 0 auto; }
    .kind { font-family: ui-monospace, Menlo, monospace; font-size: 9px; padding: 2px 6px; border-radius: 4px; background: #efede7; color: #6d7280; }
    .kind.api { background: var(--blue-soft); color: var(--blue); }
    .kind.ui { background: var(--purple-soft); color: var(--purple); }
    .kind.sql { background: var(--amber-soft); color: var(--amber); }
    .kind.test { background: var(--green-soft); color: var(--green); }
    .lines { font-family: ui-monospace, Menlo, monospace; font-size: 10px; color: var(--muted); }
    .codeblock { background: #20262b; border-radius: 10px; padding: 16px; overflow-x: auto; margin-top: 10px; }
    .codeblock pre { margin: 0; color: #e8e4d9; font-family: ui-monospace, Menlo, monospace; font-size: 10.5px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
    table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
    th { text-align: left; font-size: 10px; letter-spacing: .06em; color: var(--muted); font-weight: 600; padding: 8px 8px; border-bottom: 1px solid var(--line); }
    td { padding: 10px 8px; border-bottom: 1px solid #f0ebe0; }
    tr:last-child td { border-bottom: 0; }
    .mono { font-family: ui-monospace, Menlo, monospace; font-size: 11px; color: #4b5563; }
    .num { text-align: right; font-variant-numeric: tabular-nums; }
    .action { text-align: right; color: var(--green); font-size: 11px; font-weight: 650; }
    .demo-link { display: inline-block; background: var(--green); color: #fff; text-decoration: none; padding: 9px 14px; border-radius: 8px; font-size: 12.5px; font-weight: 650; }
    .pill { font-size: 10px; font-weight: 650; padding: 3px 8px; border-radius: 999px; }
    .pill.paid { background: var(--green-soft); color: var(--green); }
    .pill.pending { background: var(--amber-soft); color: var(--amber); }
    .pill.refunded { background: #efede7; color: var(--muted); }
    .dial { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
    .mode { border: 1px solid var(--line); border-radius: 10px; padding: 12px; background: #fffefa; }
    .mode.active { border-color: #bcd9c6; background: var(--green-soft); }
    .mode .name { font-family: ui-monospace, Menlo, monospace; font-size: 10px; letter-spacing: .08em; color: var(--muted); margin-bottom: 3px; }
    .mode .title { font-weight: 700; font-size: 13px; margin-bottom: 2px; }
    .mode .desc { font-size: 10.5px; color: var(--muted); line-height: 1.5; }
    .footer { margin-top: 24px; font-size: 11px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 8px 16px; }
    .hash { font-family: ui-monospace, Menlo, monospace; background: #eae5d9; padding: 3px 7px; border-radius: 5px; }
    @media (max-width: 640px) {
      .metrics { grid-template-columns: repeat(2, 1fr); }
      .dial { grid-template-columns: 1fr; }
      h1 { font-size: 25px; }
      .wrap { padding: 24px 15px 56px; }
      .panel { padding: 18px 16px; }
    }
    """

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PROUT Patterns — {esc(app.name)}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <div class="kicker">PROUT PATTERNS / BUILD REPORT</div>
  <h1>{esc(app.name)}</h1>
  <div class="sub">パターンから展開されたアプリケーション</div>
  <div class="chips">{''.join(f'<span class="chip">{esc(s)}</span>' for s in app.stack)}<span class="chip">freedom: {esc(app.freedom)}</span></div>

  <div class="metrics">
    <div class="metric"><div class="value">{spec}</div><div class="label">SPEC LINES</div></div>
    <div class="metric"><div class="value accent">{total_lines:,}</div><div class="label">GENERATED LINES (EST.)</div></div>
    <div class="metric"><div class="value">{ratio}×</div><div class="label">EXPANSION</div></div>
    <div class="metric"><div class="value">{patterns}</div><div class="label">PATTERNS / {len(errors)} ERRORS</div></div>
  </div>

  <div class="panel">
    <h2>AIが書いたもの</h2>
    <div class="src">{spec_html}</div>
  </div>

  <div class="panel">
    <h2>展開されたもの</h2>
    {''.join(group_html)}
  </div>

  <div class="panel">
    <h2>人間が見るもの</h2>
    {human_panel}
  </div>

  <div class="panel">
    <h2>自由度ダイヤル</h2>
    <p style="font-size:13px;color:var(--muted);">「速いが縛られる」と「自由だが遅い」を、プロジェクトごとに選ぶ。現在は <strong>{esc(app.freedom)}</strong>。{esc(freedom_text)}</p>
    <div class="dial">
      <div class="mode {'active' if app.freedom == 'strict' else ''}"><div class="name">STRICT</div><div class="title">パターンのみ</div><div class="desc">最速。全生成物がパターン由来で、検証が最も簡単。</div></div>
      <div class="mode {'active' if app.freedom == 'guided' else ''}"><div class="name">GUIDED</div><div class="title">パターン + custom</div><div class="desc">customにはwhyが必須。既存業務ロジックとの接続向け。</div></div>
      <div class="mode {'active' if app.freedom == 'open' else ''}"><div class="name">OPEN</div><div class="title">任意コード</div><div class="desc">自由度最大。検証はPROUTの契約層に委ねる。</div></div>
    </div>
  </div>

  <div class="footer">
    <span>Spec → patterns → artifacts. {spec} lines in, {total_lines:,} lines out (estimated).</span>
    <span>AST <span class="hash">{esc(app.name)}-{spec}-{total_lines}</span></span>
  </div>
</div>
</body>
</html>
"""


def sample_value(name: str, typ: str) -> str:
    n = name.lower()
    t = typ.lower()
    if "email" in n or t == "email":
        return "taro@example.com"
    if "end" in n and ("date" in n or t == "date"):
        return "2026-12-09"
    if "date" in n or t == "date":
        return "2026-12-08"
    if "amount" in n or "price" in n or "total" in n or t == "money":
        return "48000"
    if "quantity" in n or "count" in n:
        return "3"
    if "department" in n:
        return "営業部"
    if "item" in n:
        return "ノートPC"
    if "applicant" in n:
        return "山田 太郎"
    if "destination" in n:
        return "大阪"
    if "purpose" in n or "reason" in n:
        return "顧客訪問"
    if "note" in n or "memo" in n:
        return "新規取引先との打ち合わせ"
    if "model" in n:
        return "ThinkPad X1"
    if "name" in n:
        return "山田 太郎"
    return "サンプル"


def render_demo(app: App, arts: list[Artifact]) -> str:
    """Generate a dependency-free, interactive approval demo for humans."""
    approval = next((p for p in app.patterns if p.name == "approval"), None)
    if approval is None:
        return ""

    entity_name = approval.slots.get("entity") or approval.target
    entity = next((e for e in app.entities if e.name == entity_name), None)
    approver = approval.slots.get("approver", "approver")
    on_approved = approval.slots.get("on_approved", "")
    custom = next((c for c in app.customs if c.name == on_approved), None)
    if custom and custom.why:
        action = custom.why
    elif on_approved:
        action = f"{on_approved} を実行する"
    else:
        action = "承認後の処理は未定義"

    fields = []
    if entity:
        for name, typ in entity.fields.items():
            if name in ("id", "status"):
                continue
            fields.append({"name": name, "type": typ, "value": sample_value(name, typ)})

    fields_html = "".join(
        f'<label class="field"><span>{esc(f["name"])} <em>{esc(f["type"])}</em></span>'
        f'<input value="{esc(f["value"])}" data-field="{esc(f["name"])}"></label>'
        for f in fields
    )

    data = {
        "app": app.name,
        "entity": entity_name,
        "approver": approver,
        "onApproved": on_approved,
        "action": action,
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    demo_css = """
    :root {
      --bg: #f4f1e9; --card: #fffdf8; --ink: #1e2328; --muted: #6d7280;
      --line: #e3ddd0; --green: #2f6f4e; --green-soft: #e7f2ea;
      --amber: #a85a08; --amber-soft: #fbf1e0; --red: #b3261e; --red-soft: #fbe9e7;
      --blue: #2454b8; --blue-soft: #e8eefb;
      --shadow: 0 1px 2px rgba(30,35,40,.04), 0 8px 24px rgba(30,35,40,.05);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; color: var(--ink); line-height: 1.7;
      background: radial-gradient(1200px 500px at 50% -200px, #fbf8f1 0%, rgba(251,248,241,0) 70%), var(--bg);
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", "Yu Gothic", sans-serif;
      -webkit-font-smoothing: antialiased;
    }
    .wrap { max-width: 980px; margin: 0 auto; padding: 30px 20px 64px; }
    .kicker { font-family: ui-monospace, Menlo, monospace; font-size: 11px; letter-spacing: .16em; color: var(--green); margin-bottom: 10px; }
    h1 { font-size: 32px; line-height: 1.25; margin: 0 0 6px; letter-spacing: -.02em; }
    .sub { color: var(--muted); font-size: 14px; margin: 0 0 22px; }
    .sub code { background: #eae5d9; padding: 2px 6px; border-radius: 5px; font-size: .88em; }
    .grid { display: grid; grid-template-columns: 1.12fr .88fr; gap: 16px; align-items: start; }
    .card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 20px; box-shadow: var(--shadow); margin-bottom: 16px; }
    .card-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 14px; }
    .card-head strong { font-size: 15px; }
    .card-head code { font-family: ui-monospace, Menlo, monospace; font-size: 10.5px; background: #f2eee4; padding: 3px 7px; border-radius: 5px; color: #4b5563; }
    .field { display: block; margin-bottom: 10px; }
    .field span { display: flex; align-items: center; justify-content: space-between; font-size: 11px; color: var(--muted); margin-bottom: 4px; }
    .field em { font-style: normal; font-family: ui-monospace, Menlo, monospace; font-size: 9px; color: #8a8f98; }
    .field input { width: 100%; border: 1px solid var(--line); background: #fffefa; border-radius: 8px; padding: 8px 10px; font: inherit; font-size: 13px; color: var(--ink); }
    .field input:focus { outline: 2px solid #bcd9c6; outline-offset: 1px; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
    button { border: 0; border-radius: 8px; padding: 9px 14px; font: inherit; font-size: 12.5px; font-weight: 650; cursor: pointer; background: var(--green); color: #fff; transition: transform .08s ease, opacity .15s ease; }
    button:active { transform: translateY(1px); }
    button:disabled { opacity: .32; cursor: not-allowed; }
    button.ghost { background: transparent; color: var(--muted); border: 1px solid var(--line); }
    .pill { font-family: ui-monospace, Menlo, monospace; font-size: 10px; font-weight: 700; padding: 4px 9px; border-radius: 999px; letter-spacing: .04em; }
    .pill.draft { background: #efede7; color: #6d7280; }
    .pill.pending { background: var(--amber-soft); color: var(--amber); }
    .pill.approved { background: var(--green-soft); color: var(--green); }
    .pill.returned { background: var(--red-soft); color: var(--red); }
    .pill.rejected { background: var(--red-soft); color: var(--red); }
    .flow { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 11.5px; color: #4b5563; margin-bottom: 14px; }
    .flow span { background: #f8f5ee; border: 1px solid var(--line); border-radius: 999px; padding: 5px 9px; }
    .flow i { color: #9aa0a8; font-style: normal; }
    .action-box { background: var(--green-soft); border: 1px solid #cfe3d6; border-radius: 12px; padding: 14px; }
    .action-box .label { font-size: 10px; letter-spacing: .1em; color: var(--green); font-weight: 700; margin-bottom: 4px; }
    .action-text { font-size: 13px; font-weight: 650; }
    .state { display: inline-block; margin-top: 8px; font-family: ui-monospace, Menlo, monospace; font-size: 10px; padding: 3px 8px; border-radius: 999px; background: #fffefa; color: var(--muted); border: 1px solid var(--line); }
    .state.done { background: var(--green); color: #fff; border-color: var(--green); }
    .log { list-style: none; margin: 0; padding: 0; }
    .log li { display: flex; align-items: baseline; gap: 10px; padding: 8px 0; border-bottom: 1px dashed #ece6d9; font-size: 12px; }
    .log li:last-child { border-bottom: 0; }
    .log .time { font-family: ui-monospace, Menlo, monospace; font-size: 10px; color: #8a8f98; min-width: 42px; }
    .log .n { margin-left: auto; font-family: ui-monospace, Menlo, monospace; font-size: 9px; color: #b0b4ba; }
    .log li.ok span:nth-child(2) { color: var(--green); font-weight: 650; }
    .log li.warn span:nth-child(2) { color: var(--amber); }
    .log li.empty { color: var(--muted); font-size: 11.5px; }
    .compare { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .compare-col { border: 1px solid var(--line); border-radius: 12px; padding: 14px; background: #fffefa; opacity: .55; transition: opacity .2s ease, border-color .2s ease, background .2s ease; }
    .compare-col.active { opacity: 1; border-color: #bcd9c6; background: var(--green-soft); }
    .compare-label { font-size: 10px; letter-spacing: .1em; color: var(--muted); font-weight: 700; margin-bottom: 8px; }
    .compare-col ul { margin: 0; padding-left: 16px; font-size: 11.5px; color: #4b5563; }
    .compare-col li { margin: 4px 0; }
    .stats { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 18px; }
    .stat { background: #fffefa; border: 1px solid var(--line); border-radius: 999px; padding: 4px 10px; font-size: 10.5px; color: var(--muted); }
    .stat b { color: var(--ink); font-weight: 700; }
    .footer { margin-top: 8px; font-size: 11px; color: var(--muted); }
    @media (max-width: 760px) {
      .grid { grid-template-columns: 1fr; }
      h1 { font-size: 26px; }
      .wrap { padding: 24px 15px 56px; }
      .card { padding: 16px; }
      .compare { grid-template-columns: 1fr; }
    }
    """

    demo_js = """
    const statusEl = document.getElementById("status");
    const submitEl = document.getElementById("submit");
    const approveEl = document.getElementById("approve");
    const returnEl = document.getElementById("return");
    const rejectEl = document.getElementById("reject");
    const resetEl = document.getElementById("reset");
    const actionStateEl = document.getElementById("action-state");
    const logEl = document.getElementById("log");
    const beforeCol = document.getElementById("before-col");
    const afterCol = document.getElementById("after-col");
    const auditCountEl = document.getElementById("audit-count");

    let state = { status: "draft", fired: false, log: [] };

    function addLog(text, tone) {
      state.log.push({
        text: text,
        tone: tone || "",
        time: new Date().toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" })
      });
      render();
    }

    function render() {
      statusEl.textContent = state.status;
      statusEl.className = "pill " + state.status;
      submitEl.disabled = state.status !== "draft";
      approveEl.disabled = state.status !== "pending";
      returnEl.disabled = state.status !== "pending";
      rejectEl.disabled = state.status !== "pending";
      actionStateEl.textContent = state.fired ? "実行済み" : "未実行";
      actionStateEl.className = "state" + (state.fired ? " done" : "");
      beforeCol.classList.toggle("active", state.status !== "approved");
      afterCol.classList.toggle("active", state.status === "approved");
      auditCountEl.textContent = String(state.log.length);
      document.querySelectorAll("input[data-field]").forEach(function (el) {
        el.disabled = state.status !== "draft";
      });
      if (state.log.length === 0) {
        logEl.innerHTML = '<li class="empty">まだ操作はありません。「申請する」を押してください。</li>';
        return;
      }
      logEl.innerHTML = state.log.map(function (entry, index) {
        return '<li class="' + entry.tone + '"><span class="time">' + entry.time +
          '</span><span>' + entry.text + '</span><span class="n">' +
          String(index + 1).padStart(2, "0") + '</span></li>';
      }).join("");
    }

    submitEl.addEventListener("click", function () {
      state.status = "pending";
      addLog("申請者が申請を送信しました", "info");
      addLog("承認者（" + DATA.approver + "）に承認依頼を送りました", "info");
    });

    approveEl.addEventListener("click", function () {
      state.status = "approved";
      addLog("承認者が申請を承認しました", "ok");
      state.fired = true;
      addLog("承認後の処理を実行: " + DATA.action, "ok");
    });

    returnEl.addEventListener("click", function () {
      state.status = "returned";
      addLog("承認者が申請を差し戻しました", "warn");
    });

    rejectEl.addEventListener("click", function () {
      state.status = "rejected";
      addLog("承認者が申請を却下しました", "warn");
    });

    resetEl.addEventListener("click", function () {
      state = { status: "draft", fired: false, log: [] };
      render();
    });

    render();
    """

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PROUT Patterns — {esc(app.name)} / approval demo</title>
<style>{demo_css}</style>
</head>
<body>
<div class="wrap">
  <div class="kicker">PROUT PATTERNS / HUMAN VIEW</div>
  <h1>{esc(app.name)}</h1>
  <p class="sub">承認前と承認後を、ボタンで確かめる。仕様の <code>approval</code> と <code>{esc(on_approved) or "on_approved"}</code> が、この画面の動きに対応している。</p>

  <div class="stats">
    <span class="stat">対象 <b>{esc(entity_name)}</b></span>
    <span class="stat">承認者 <b>{esc(approver)}</b></span>
    <span class="stat">監査ログ <b id="audit-count">0</b> 件</span>
  </div>

  <div class="grid">
    <section class="card">
      <div class="card-head"><strong>申請</strong><span id="status" class="pill draft">draft</span></div>
      {fields_html}
      <div class="actions">
        <button id="submit">申請する</button>
        <button id="approve" disabled>承認する</button>
        <button id="return" class="ghost" disabled>差し戻す</button>
        <button id="reject" class="ghost" disabled>却下する</button>
        <button id="reset" class="ghost">リセット</button>
      </div>
    </section>

    <div>
      <section class="card">
        <div class="card-head"><strong>承認フロー</strong><code>{esc(approver)}</code></div>
        <div class="flow">
          <span>申請</span><i>→</i><span>{esc(approver)} が承認</span><i>→</i><span>{esc(action)}</span>
        </div>
        <div class="action-box">
          <div class="label">ON_APPROVED</div>
          <div class="action-text">{esc(action)}</div>
          <div id="action-state" class="state">未実行</div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><strong>履歴</strong><span style="font-size:11px;color:var(--muted)">操作するたびに増える</span></div>
        <ol id="log" class="log"><li class="empty">まだ操作はありません。「申請する」を押してください。</li></ol>
      </section>
    </div>
  </div>

  <section class="card">
    <div class="card-head"><strong>承認前後の差分</strong><span style="font-size:11px;color:var(--muted)">状態に応じて強調される</span></div>
    <div class="compare">
      <div class="compare-col active" id="before-col">
        <div class="compare-label">承認前 / BEFORE</div>
        <ul>
          <li>申請は下書き、または承認待ち</li>
          <li>承認後の処理はまだ動かない</li>
          <li>監査ログは申請と承認依頼だけ</li>
        </ul>
      </div>
      <div class="compare-col" id="after-col">
        <div class="compare-label">承認後 / AFTER</div>
        <ul>
          <li>状態は <code>approved</code></li>
          <li>{esc(action)}</li>
          <li>監査ログに承認と処理を記録</li>
        </ul>
      </div>
    </div>
  </section>

  <div class="footer">PROUT Patterns / generated from the spec. No server, no external dependencies.</div>
</div>
<script>
const DATA = {data_json};
{demo_js}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Discovery and benchmark
# ---------------------------------------------------------------------------

def find_patterns(query: str, registry: dict[str, PatternManifest]) -> list[tuple[int, PatternManifest]]:
    tokens = [t.lower() for t in re.split(r"\s+", query) if t]
    results: list[tuple[int, PatternManifest]] = []
    for m in registry.values():
        score = 0
        name = m.name.lower()
        intent = m.intent.lower()
        tags = [t.lower() for t in m.tags]
        slots = [s.lower() for s in m.slots]
        for token in tokens:
            if token in name:
                score += 5
            if token in intent:
                score += 3
            for tag in tags:
                if token in tag:
                    score += 3
            for slot in slots:
                if token in slot:
                    score += 1
        if score:
            results.append((score, m))
    return sorted(results, key=lambda x: (-x[0], x[1].name))


def bench_rows(registry: dict[str, PatternManifest]) -> list[dict]:
    cases = [
        ("expense_before", ROOT / "bench" / "expense_before.prout"),
        ("expense_after", ROOT / "bench" / "expense_after.prout"),
        ("travel_request", ROOT / "examples" / "travel_request.prout"),
        ("equipment_purchase", ROOT / "examples" / "equipment_purchase.prout"),
        ("pc_lending", ROOT / "bench" / "pc_lending.prout"),
    ]
    rows = []
    for name, path in cases:
        app = parse_app(path.read_text(encoding="utf-8"))
        diags = validate(app, registry)
        arts = expand(app, registry)
        spec = spec_lines(app.source)
        custom = sum(c.lines for c in app.customs)
        ai_lines = spec + custom
        generated = sum(a.lines for a in arts)
        rows.append(
            {
                "case": name,
                "spec_lines": spec,
                "custom_lines": custom,
                "ai_authored_lines": ai_lines,
                "generated_lines": generated,
                "reuse_ratio": round(generated / ai_lines, 1) if ai_lines else 0,
                "patterns": [p.name for p in app.patterns],
                "errors": len([d for d in diags if d.severity == "error"]),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_build(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry))
    text = Path(args.file).read_text(encoding="utf-8")
    try:
        app = parse_app(text)
    except ValueError as ex:
        print(f"[ERROR] {ex}", file=sys.stderr)
        return 2

    diags = validate(app, registry)
    arts = expand(app, registry)
    total = sum(a.lines for a in arts)
    spec = spec_lines(text)

    print(f"PROUT Patterns: {app.name}")
    print(f"stack: {', '.join(app.stack)}  freedom: {app.freedom}")
    print(f"entities: {len(app.entities)}  patterns: {len(app.patterns)}  custom: {len(app.customs)}")
    print(f"spec lines: {spec}  generated lines: {total:,}  expansion: {round(total/spec, 1) if spec else 0}x")
    if diags:
        print()
        for d in diags:
            print(d.render())
    else:
        print("OK — no issues")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = render_report(app, diags, arts)
    (out / "report.html").write_text(report, encoding="utf-8")
    has_errors = any(d.severity == "error" for d in diags)
    demo = "" if has_errors else render_demo(app, arts)
    if demo:
        (out / "demo.html").write_text(demo, encoding="utf-8")

    manifest = {
        "app": app.name,
        "stack": app.stack,
        "freedom": app.freedom,
        "spec_lines": spec,
        "generated_lines": total,
        "patterns": [{"name": p.name, "target": p.target, "slots": p.slots} for p in app.patterns],
        "artifacts": [
            {"pattern": a.pattern, "path": a.path, "kind": a.kind, "lines": a.lines, "desc": a.desc}
            for a in arts
        ],
        "diagnostics": [d.render() for d in diags],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out / 'report.html'}")
    if demo:
        print(f"wrote {out / 'demo.html'}")
    elif has_errors:
        print("demo.html skipped because the spec has errors")
    print(f"wrote {out / 'manifest.json'}")
    return 1 if any(d.severity == "error" for d in diags) else 0


def cmd_find(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry))
    results = find_patterns(args.query, registry)
    if not results:
        print(f"no patterns matched {args.query!r}")
        return 1
    print(f"query: {args.query!r}  ({len(results)} match(es))")
    for score, m in results:
        required = [k for k, v in m.slots.items() if v.get("required")]
        optional = [k for k, v in m.slots.items() if not v.get("required")]
        print()
        print(f"  {m.name}  v{m.version}  score={score}")
        print(f"    intent: {m.intent}")
        print(f"    required: {', '.join(required) or '-'}")
        print(f"    optional: {', '.join(optional) or '-'}")
        print(f"    tags: {', '.join(m.tags)}")
        if m.examples:
            print(f"    examples: {', '.join(m.examples)}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    registry_dir = Path(args.registry)
    data = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    m = manifest_from_dict(data)
    errors = validate_manifest(m)
    if errors:
        print("[ERROR] invalid manifest:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    dest = registry_dir / f"{m.name}.json"
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index = write_index(registry_dir)
    print(f"added {m.name} v{m.version} -> {dest}")
    print(f"updated {index}")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    out = write_index(Path(args.registry))
    registry = load_registry(Path(args.registry))
    print(f"indexed {len(registry)} pattern(s) -> {out}")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry))
    rows = bench_rows(registry)
    print(f"{'case':<16} {'spec':>5} {'custom':>7} {'AI lines':>9} {'generated':>10} {'reuse':>7}  patterns")
    for r in rows:
        print(
            f"{r['case']:<16} {r['spec_lines']:>5} {r['custom_lines']:>7} "
            f"{r['ai_authored_lines']:>9} {r['generated_lines']:>10} "
            f"{r['reuse_ratio']:>6}x  {', '.join(r['patterns'])}"
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="proutc")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="validate and expand an app spec")
    b.add_argument("file")
    b.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    b.add_argument("--out", default=str(ROOT / "build"))
    b.set_defaults(func=cmd_build)

    f = sub.add_parser("find", help="search the registry")
    f.add_argument("query")
    f.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    f.set_defaults(func=cmd_find)

    a = sub.add_parser("add", help="add a pattern manifest to the registry")
    a.add_argument("manifest")
    a.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    a.set_defaults(func=cmd_add)

    i = sub.add_parser("index", help="rebuild registry/index.json")
    i.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    i.set_defaults(func=cmd_index)

    be = sub.add_parser("bench", help="compare apps with and without reusable patterns")
    be.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    be.add_argument("--out", default=str(ROOT / "bench" / "report.json"))
    be.set_defaults(func=cmd_bench)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
