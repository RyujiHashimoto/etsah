"""Pattern compiler: slots, find scoring, index consistency, and build output."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
import proutc
from proutc import (
    DEFAULT_REGISTRY,
    expand,
    find_patterns,
    load_registry,
    manifest_from_dict,
    parse_app,
    spec_lines,
    validate,
    validate_manifest,
    write_index,
    cmd_add,
    cmd_build,
)
from support import PATTERNS, snippet


def _quiet(func, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return func(*args)


def _codes(diags, code: str | None = None) -> list[str]:
    found = [d.code for d in diags]
    if code is None:
        return found
    return [item for item in found if item == code]


def _manifest(name: str, **overrides) -> proutc.PatternManifest:
    data = {
        "name": name,
        "version": "0.1.0",
        "intent": overrides.pop("intent", "do a thing"),
        "tags": overrides.pop("tags", []),
        "slots": overrides.pop("slots", {}),
        "effects": overrides.pop("effects", []),
        "artifacts": overrides.pop("artifacts", [{"path": "a.ts", "kind": "TS", "lines": 1, "desc": "a"}]),
        "examples": [],
        "provenance": {},
    }
    data.update(overrides)
    return manifest_from_dict(data)


class TestPatternParse(unittest.TestCase):
    def test_app_entities_patterns_and_custom(self) -> None:
        app = parse_app(
            snippet(
                """
                # ignored
                app shop
                  stack nextjs, postgres
                  freedom open

                  entity Order
                    id: Id
                    total: Money

                  pattern notify
                    channel: "email"

                  custom extra
                    why "because \\"legacy\\""
                    lines 3
                """
            )
        )
        self.assertEqual(app.name, "shop")
        self.assertEqual(app.stack, ["nextjs", "postgres"])
        self.assertEqual(app.freedom, "open")
        self.assertEqual(app.entities[0].fields, {"id": "Id", "total": "Money"})
        # Slot values stay raw. Only `why` is a JSON string.
        self.assertEqual(app.patterns[0].slots["channel"], '"email"')
        self.assertEqual(app.customs[0].why, 'because "legacy"')
        self.assertEqual(app.customs[0].lines, 3)

    def test_freedom_defaults_to_guided(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                """
            )
        )
        self.assertEqual(app.freedom, "guided")
        self.assertEqual(app.patterns, [])

    def test_comments_do_not_count_as_spec_lines(self) -> None:
        self.assertEqual(spec_lines("# c\n\napp demo\n  stack nextjs\n"), 2)

    def test_indent_and_tabs(self) -> None:
        with self.assertRaises(ValueError) as odd:
            parse_app("app demo\n stack nextjs\n")
        self.assertIn("line 2", str(odd.exception))

        with self.assertRaises(ValueError) as tabs:
            parse_app("app demo\n\tstack nextjs\n")
        self.assertIn("line 2", str(tabs.exception))

        with self.assertRaises(ValueError) as field:
            parse_app(snippet(
                """
                app demo
                  stack nextjs
                  entity Order
                  id: Id
                """
            ))
        self.assertIn("line 4", str(field.exception))

    def test_empty_and_missing_header(self) -> None:
        with self.assertRaises(ValueError):
            parse_app("")
        with self.assertRaises(ValueError):
            parse_app("entity Order\n")


class TestSlots(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry(DEFAULT_REGISTRY)

    def test_required_slots_match_the_pattern_table(self) -> None:
        # PATTERNS.md, 「パターンライブラリ」.
        expected = {
            "auth": {"provider"},
            "crud": {"entity"},
            "checkout": {"entity", "payment"},
            "refund": {"entity", "payment"},
            "dashboard": {"entity", "metrics"},
            "search": {"entity", "fields"},
            "notify": {"channel"},
            "upload": {"entity", "field"},
            "approval": {"entity", "approver"},
            "scene": {"title", "body"},
            "activity_log": {"entity", "events"},
            "archive": {"entity", "timestamp"},
            "assignment": {"entity", "assignee"},
            "checklist": {"entity", "template"},
            "comment_thread": {"entity", "author"},
            "csv_export": {"entity", "columns"},
            "due_date": {"entity", "deadline"},
            "labels": {"entity", "vocabulary"},
            "survey": {"entity", "scale"},
            "work_queue": {"entity", "priority"},
            "timesheet": {"entity", "approver"},
            "dispatch_contract": {"entity", "staff", "client", "start_date", "end_date", "conflict_date"},
            "staffing_billing": {"entity", "timesheet", "bill_rate", "pay_rate"},
            "matching": {"staff", "opening", "skills"},
            "overtime_agreement": {
                "entity",
                "timesheet",
                "warn_at_hours",
                "on_warning",
                "on_limit",
            },
            "ai_task": {
                "entity",
                "trigger",
                "steps",
                "effects",
                "audience",
                "spend_per_run",
                "spend_per_period",
                "period",
                "net_per_hour",
                "emit_per_hour",
                "retries",
                "escalate_to",
                "idempotency_key",
                "audit",
            },
            "approval_gate": {"entity", "approver", "action", "audience"},
        }
        self.assertEqual(set(self.registry), set(expected))
        for name, slots in expected.items():
            required = {key for key, spec in self.registry[name].slots.items() if spec.get("required")}
            self.assertEqual(required, slots, name)

    def test_missing_required_slots_and_unknown_pattern(self) -> None:
        missing_provider = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  pattern auth
                """
            )
        )
        diags = validate(missing_provider, self.registry)
        self.assertEqual(_codes(diags), ["E-SLOT"])

        unknown = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  pattern does_not_exist
                """
            )
        )
        self.assertEqual(_codes(validate(unknown, self.registry)), ["E-PATTERN"])

    def test_entity_must_be_declared_and_header_does_not_fill_the_slot(self) -> None:
        missing_entity = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  pattern refund
                    payment: stripe
                """
            )
        )
        diags = validate(missing_entity, self.registry)
        self.assertIn("E-SLOT", _codes(diags))
        self.assertIn("E-TARGET", _codes(diags))

        undeclared = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  pattern refund
                    entity: Order
                    payment: stripe
                """
            )
        )
        self.assertIn("E-TARGET", _codes(validate(undeclared, self.registry)))

        # `pattern refund Order` supplies a target name, but the required
        # `entity` slot is still missing. The header is only a fallback target.
        header_only = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity Order
                    id: Id
                    total: Money
                    status: String
                  pattern refund Order
                    payment: stripe
                """
            )
        )
        codes = _codes(validate(header_only, self.registry))
        self.assertIn("E-SLOT", codes)
        self.assertNotIn("E-TARGET", codes)

    def test_requires_fields_by_name(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity Order
                    id: Id
                  pattern refund
                    entity: Order
                    payment: stripe
                """
            )
        )
        diags = validate(app, self.registry)
        self.assertEqual(_codes(diags, "E-ENTITY"), ["E-ENTITY", "E-ENTITY"])

        complete = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity Order
                    id: Id
                    total: Money
                    status: String
                  pattern refund
                    entity: Order
                    payment: stripe
                """
            )
        )
        self.assertEqual(validate(complete, self.registry), [])

    def test_conflicts_freedom_and_custom(self) -> None:
        twice_auth = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  pattern auth
                    provider: clerk
                  pattern auth
                    provider: clerk
                """
            )
        )
        self.assertIn("E-CONFLICT", _codes(validate(twice_auth, self.registry)))

        same_order = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity Order
                    id: Id
                    total: Money
                    status: String
                  pattern refund
                    entity: Order
                    payment: stripe
                  pattern refund
                    entity: Order
                    payment: stripe
                """
            )
        )
        self.assertIn("E-CONFLICT", _codes(validate(same_order, self.registry)))

        two_orders = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity Order
                    id: Id
                    total: Money
                    status: String
                  entity Invoice
                    id: Id
                    total: Money
                    status: String
                  pattern refund
                    entity: Order
                    payment: stripe
                  pattern refund
                    entity: Invoice
                    payment: stripe
                """
            )
        )
        self.assertNotIn("E-CONFLICT", _codes(validate(two_orders, self.registry)))

        strict = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  freedom strict
                  custom extra
                    why "no"
                    lines 4
                """
            )
        )
        self.assertIn("E-FREEDOM", _codes(validate(strict, self.registry)))

        guided = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  freedom guided
                  custom extra
                    lines 4
                """
            )
        )
        self.assertIn("E-CUSTOM", _codes(validate(guided, self.registry)))

        guided_ok = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  freedom guided
                  custom extra
                    why "connect the ledger"
                    lines 4
                """
            )
        )
        self.assertEqual(validate(guided_ok, self.registry), [])

        opened = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  freedom open
                  custom extra
                    lines 4
                """
            )
        )
        self.assertEqual(validate(opened, self.registry), [])

        bad_lines = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  freedom open
                  custom extra
                    lines 0
                """
            )
        )
        self.assertIn("E-CUSTOM", _codes(validate(bad_lines, self.registry)))

    def test_stack_freedom_and_duplicates(self) -> None:
        no_stack = parse_app("app demo\n  freedom guided\n")
        self.assertIn("E-STACK", _codes(validate(no_stack, self.registry)))

        bad_freedom = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  freedom loose
                """
            )
        )
        self.assertIn("E-FREEDOM", _codes(validate(bad_freedom, self.registry)))

        dup = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity Order
                  entity Order
                    id: Id
                  custom extra
                    why "once"
                    lines 1
                  custom extra
                    why "twice"
                    lines 1
                """
            )
        )
        codes = _codes(validate(dup, self.registry))
        self.assertGreaterEqual(codes.count("E-DUP"), 2)
        self.assertIn("E-ENTITY", codes)


class TestFind(unittest.TestCase):
    def test_score_weights_and_ordering(self) -> None:
        registry = {
            "alpha": _manifest("alpha", intent="ship widgets", tags=["widget"], slots={"entity": {"type": "entity"}}),
            "beta": _manifest("beta", intent="ship widgets", tags=["other"]),
            "box": _manifest("box", intent="nothing", tags=["refund-admin", "admin-tool"]),
        }
        ranked = find_patterns("widget", registry)
        self.assertEqual([(score, item.name) for score, item in ranked], [(6, "alpha"), (3, "beta")])
        # Name match is +5 and does not also require a tag hit.
        self.assertEqual(find_patterns("alpha", registry)[0][0], 5)
        # Equal scores break ties by name.
        tied = find_patterns("ship", registry)
        self.assertEqual([item.name for _score, item in tied], ["alpha", "beta"])
        self.assertEqual(tied[0][0], tied[1][0])
        # Each matching tag adds 3, and matching is case-insensitive.
        self.assertEqual(find_patterns("ADMIN", registry)[0][0], 6)
        self.assertEqual(find_patterns("entity", registry)[0][0], 1)
        self.assertEqual(find_patterns("", registry), [])
        self.assertEqual(find_patterns("no-such-pattern", registry), [])
        # Substring, not a word boundary: "app" hits "approval".
        approval = {"approval": _manifest("approval", intent="no")}
        self.assertGreaterEqual(find_patterns("app", approval)[0][0], 5)

    def test_documented_query_returns_approval_at_score_15(self) -> None:
        found = find_patterns("申請 承認", load_registry(DEFAULT_REGISTRY))
        self.assertTrue(found)
        self.assertEqual(found[0][1].name, "approval")
        self.assertEqual(found[0][0], 15)

    def test_find_command(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(PATTERNS / "proutc.py"), "find", "申請 承認"],
            cwd=PATTERNS,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("approval", proc.stdout)
        self.assertIn("score=15", proc.stdout)

        missing = subprocess.run(
            [sys.executable, str(PATTERNS / "proutc.py"), "find", "no-such-pattern"],
            cwd=PATTERNS,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(missing.returncode, 1)


class TestIndex(unittest.TestCase):
    def test_committed_index_matches_manifests(self) -> None:
        registry = load_registry(DEFAULT_REGISTRY)
        committed = json.loads((DEFAULT_REGISTRY / "index.json").read_text(encoding="utf-8"))
        rebuilt = {"version": 1, "patterns": [registry[name].to_index() for name in sorted(registry)]}
        self.assertEqual(committed, rebuilt)
        self.assertEqual([item["name"] for item in committed["patterns"]], sorted(registry))

    def test_write_index_is_idempotent_and_skips_index_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "registry"
            dest.mkdir()
            for path in DEFAULT_REGISTRY.glob("*.json"):
                if path.name == "index.json":
                    continue
                (dest / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            first = write_index(dest).read_text(encoding="utf-8")
            second = write_index(dest).read_text(encoding="utf-8")
            self.assertEqual(first, second)
            loaded = load_registry(dest)
            self.assertEqual(set(loaded), set(load_registry(DEFAULT_REGISTRY)))
            self.assertEqual(json.loads(first), json.loads((DEFAULT_REGISTRY / "index.json").read_text(encoding="utf-8")))

    def test_invalid_manifest_is_rejected(self) -> None:
        errors = validate_manifest(_manifest("Bad"))
        self.assertTrue(any("lower_snake_case" in item for item in errors))

        empty = _manifest("ok", intent="")
        self.assertTrue(any("intent" in item for item in validate_manifest(empty)))

        no_type = _manifest("ok", slots={"entity": {"required": True}})
        self.assertTrue(any("type" in item for item in validate_manifest(no_type)))

        no_lines = _manifest("ok", artifacts=[{"path": "a.ts"}])
        self.assertTrue(any("line" in item for item in validate_manifest(no_lines)))

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            (dest / "bad.json").write_text(
                json.dumps({"name": "Bad", "intent": "x", "artifacts": [{"path": "a.ts", "lines": 1}]}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_registry(dest)

    def test_add_command_writes_manifest_and_index(self) -> None:
        manifest = {
            "name": "widget",
            "version": "0.1.0",
            "intent": "track widgets",
            "tags": ["widget"],
            "slots": {"entity": {"type": "entity", "required": True, "desc": "target"}},
            "effects": ["read(widgets)"],
            "artifacts": [{"path": "api/widgets.ts", "kind": "API", "lines": 10, "desc": "api"}],
            "examples": [],
            "provenance": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "registry"
            registry.mkdir()
            bad = root / "bad.json"
            bad.write_text(json.dumps({"name": "Nope"}), encoding="utf-8")
            rejected = _quiet(cmd_add, argparse.Namespace(manifest=str(bad), registry=str(registry)))
            self.assertEqual(rejected, 1)
            self.assertEqual(list(registry.glob("*.json")), [])

            good = root / "widget.json"
            good.write_text(json.dumps(manifest), encoding="utf-8")
            added = _quiet(cmd_add, argparse.Namespace(manifest=str(good), registry=str(registry)))
            self.assertEqual(added, 0)
            loaded = load_registry(registry)
            self.assertIn("widget", loaded)
            index = json.loads((registry / "index.json").read_text(encoding="utf-8"))
            self.assertEqual([item["name"] for item in index["patterns"]], ["widget"])


class TestHooks(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry(DEFAULT_REGISTRY)

    def _app(
        self,
        *,
        roles: str = "employee, manager, accounting",
        channel: str = "email",
        recipient: str | None = "accounting",
        hook: str = "notify",
        notify_count: int = 1,
        custom: str = "",
    ):
        lines = [
            "app demo",
            "  stack nextjs",
            "  entity Request",
            "    id: Id",
            "    status: String",
            "  pattern auth",
            "    provider: clerk",
            f"    roles: {roles}",
            "  pattern approval",
            "    entity: Request",
            "    approver: manager",
            f"    on_approved: {hook}",
        ]
        for _ in range(notify_count):
            lines += ["  pattern notify", f"    channel: {channel}"]
            if recipient is not None:
                lines.append(f"    recipient: {recipient}")
            lines.append("    template: approved")
        if custom:
            lines += custom.splitlines()
        return parse_app("\n".join(lines) + "\n")

    def test_notify_hook_accepts_roles_and_channel(self) -> None:
        self.assertEqual(validate(self._app(), self.registry), [])

    def test_notify_hook_rejects_unknown_role(self) -> None:
        diags = validate(self._app(roles="employee, manager"), self.registry)
        self.assertIn("E-HOOK-ARG", _codes(diags))
        self.assertTrue(any("accounting" in d.message for d in diags))

    def test_notify_hook_rejects_unknown_channel(self) -> None:
        diags = validate(self._app(channel="sms"), self.registry)
        self.assertIn("E-HOOK-ARG", _codes(diags))

    def test_notify_hook_requires_applied_pattern(self) -> None:
        diags = validate(self._app(notify_count=0), self.registry)
        self.assertIn("E-HOOK", _codes(diags))

    def test_notify_hook_requires_channel_and_recipient(self) -> None:
        diags = validate(self._app(recipient=None), self.registry)
        self.assertIn("E-HOOK-ARG", _codes(diags))

    def test_notify_hook_rejects_ambiguous_instances(self) -> None:
        diags = validate(self._app(notify_count=2), self.registry)
        self.assertIn("E-HOOK", _codes(diags))
        self.assertTrue(any("ambiguous" in d.message for d in diags))

    def test_custom_hook_is_still_allowed(self) -> None:
        custom = '  custom ship\n    why "send it"\n    lines 4'
        app = self._app(hook="ship", notify_count=0, custom=custom)
        self.assertEqual(validate(app, self.registry), [])

    def test_hook_must_reference_the_declared_pattern(self) -> None:
        app = self._app(hook="auth", notify_count=0)
        diags = validate(app, self.registry)
        self.assertIn("E-HOOK", _codes(diags))

    def test_all_hook_slots_are_checked(self) -> None:
        valid = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity Order
                    id: Id
                    total: Money
                  pattern checkout
                    entity: Order
                    payment: stripe
                    success: fulfill
                  custom fulfill
                    why "after payment"
                    lines 4
                """
            )
        )
        self.assertEqual(validate(valid, self.registry), [])

        invalid = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity Order
                    id: Id
                    total: Money
                  pattern checkout
                    entity: Order
                    payment: stripe
                    success: notify
                """
            )
        )
        diags = validate(invalid, self.registry)
        self.assertIn("E-HOOK", _codes(diags))
        self.assertTrue(any("checkout.success" in d.message for d in diags))


class TestBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry(DEFAULT_REGISTRY)

    def test_shared_artifacts_grow_with_fields(self) -> None:
        def app(*fields: str) -> proutc.App:
            lines = ["app demo", "  stack nextjs", "  entity Note"]
            lines.extend(f"    {field}" for field in fields)
            return parse_app("\n".join(lines) + "\n")

        one = expand(app("id: Id"), self.registry)
        two = expand(app("id: Id", "title: String"), self.registry)
        shared = ["db/schema.sql", "lib/db.ts", "lib/types.ts", "README.md"]
        self.assertEqual([a.path for a in one if a.pattern == "shared"], shared)
        schema = lambda arts: next(a.lines for a in arts if a.path == "db/schema.sql")
        types = lambda arts: next(a.lines for a in arts if a.path == "lib/types.ts")
        self.assertGreater(schema(two), schema(one))
        self.assertGreater(types(two), types(one))

    def test_template_substitution_and_per_field_lines(self) -> None:
        registry = {
            "box": _manifest(
                "box",
                slots={"entity": {"type": "entity", "required": True, "desc": "target"}},
                artifacts=[
                    {
                        "path": "api/{plural}/{entity_lower}.ts",
                        "kind": "API",
                        "lines": 10,
                        "per_field": 3,
                        "desc": "{Entity} rows",
                    }
                ],
            )
        }
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity Item
                    id: Id
                    name: String
                  entity Category
                    id: Id
                  pattern box
                    entity: Item
                """
            )
        )
        arts = [a for a in expand(app, registry) if a.pattern == "box"]
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0].path, "api/items/item.ts")
        self.assertEqual(arts[0].lines, 16)
        self.assertEqual(arts[0].desc, "Item rows")

        renamed = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity Category
                    id: Id
                    name: String
                  pattern box
                    entity: Category
                """
            )
        )
        path = next(a.path for a in expand(renamed, registry) if a.pattern == "box")
        self.assertEqual(path, "api/categories/category.ts")

    def test_crud_paths_follow_the_entity_slot_not_the_header(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity Order
                    id: Id
                    total: Money
                    status: String
                  pattern crud Orders
                    entity: Order
                """
            )
        )
        paths = [a.path for a in expand(app, self.registry)]
        # PATTERNS.md, 「展開の例」. "Orders" would pluralize to orderses.
        for path in (
            "api/orders/route.ts",
            "api/orders/[id]/route.ts",
            "app/(admin)/orders/page.tsx",
            "app/(admin)/orders/[id]/page.tsx",
            "lib/validation/order.ts",
            "lib/permissions.ts",
            "tests/orders.test.ts",
            "db/schema.sql#orders",
        ):
            self.assertIn(path, paths)
        self.assertNotIn("api/orderses/route.ts", paths)

        validation = next(a for a in self.registry["crud"].artifacts if a["path"].startswith("lib/validation"))
        fields = len(app.entities[0].fields)
        got = next(a.lines for a in expand(app, self.registry) if a.path.startswith("lib/validation"))
        self.assertEqual(got, int(validation["lines"]) + int(validation.get("per_field", 0)) * fields)

    def test_custom_artifact_and_determinism(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  freedom open
                  custom ship
                    lines 12
                """
            )
        )
        first = [(a.pattern, a.path, a.kind, a.lines, a.desc) for a in expand(app, self.registry)]
        second = [(a.pattern, a.path, a.kind, a.lines, a.desc) for a in expand(app, self.registry)]
        self.assertEqual(first, second)
        self.assertIn(("custom", "custom/ship.ts", "TS", 12, "custom logic"), first)

    def test_build_command_writes_report_and_demo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = root / "plain.prout"
            plain.write_text(
                snippet(
                    """
                    app mini
                      stack nextjs
                      entity Note
                        id: Id
                      pattern auth
                        provider: clerk
                    """
                ),
                encoding="utf-8",
            )
            out = root / "plain"
            code = _quiet(cmd_build, argparse.Namespace(file=str(plain), registry=str(DEFAULT_REGISTRY), out=str(out)))
            self.assertEqual(code, 0)
            self.assertGreater((out / "report.html").stat().st_size, 0)
            self.assertIn("mini", (out / "report.html").read_text(encoding="utf-8"))
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["app"], "mini")
            self.assertIn("middleware.ts", [a["path"] for a in manifest["artifacts"]])
            self.assertFalse((out / "demo.html").exists())

            approved = root / "approved.prout"
            approved.write_text(
                snippet(
                    """
                    app mini
                      stack nextjs
                      entity Note
                        id: Id
                        title: String
                        status: String
                      pattern approval
                        entity: Note
                        approver: manager
                        on_approved: ship
                      custom ship
                        why "send the widget"
                        lines 4
                    """
                ),
                encoding="utf-8",
            )
            demo_out = root / "demo"
            code = _quiet(cmd_build, argparse.Namespace(file=str(approved), registry=str(DEFAULT_REGISTRY), out=str(demo_out)))
            self.assertEqual(code, 0, "approval spec should validate")
            demo = (demo_out / "demo.html").read_text(encoding="utf-8")
            self.assertIn("manager", demo)
            self.assertIn("send the widget", demo)
            demo_manifest = json.loads((demo_out / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("api/approvals/route.ts", [a["path"] for a in demo_manifest["artifacts"]])

            broken = root / "broken.prout"
            broken.write_text("this is not an app\n", encoding="utf-8")
            self.assertEqual(
                _quiet(cmd_build, argparse.Namespace(file=str(broken), registry=str(DEFAULT_REGISTRY), out=str(root / "nope"))),
                2,
            )
            self.assertFalse((root / "nope").exists())

            incomplete = root / "incomplete.prout"
            incomplete.write_text(
                snippet(
                    """
                    app mini
                      stack nextjs
                      pattern auth
                    """
                ),
                encoding="utf-8",
            )
            bad_out = root / "bad"
            self.assertEqual(
                _quiet(cmd_build, argparse.Namespace(file=str(incomplete), registry=str(DEFAULT_REGISTRY), out=str(bad_out))),
                1,
            )
            self.assertTrue((bad_out / "report.html").is_file())
            self.assertTrue((bad_out / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
