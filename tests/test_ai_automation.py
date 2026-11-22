"""Pattern-layer checks for AI tasks and approval gates.

The contract-layer `task` syntax in docs/ai_automation.md is a proposal.
These tests lock the pattern compiler, which does not import changes into prout.py.
"""
from __future__ import annotations

import json
import textwrap
import unittest

import support  # noqa: F401
from prout import Parser, ProutError
from proutc import (
    DEFAULT_REGISTRY,
    find_patterns,
    load_registry,
    manifest_from_dict,
    parse_app,
    parse_count,
    parse_cron,
    validate,
    validate_manifest,
)
from support import PATTERNS, snippet


def _join(*parts: str) -> str:
    return "\n".join(textwrap.dedent(part).strip("\n") for part in parts)


def _app(extra: str) -> str:
    body = textwrap.indent(textwrap.dedent(extra).strip("\n"), "  ")
    header = textwrap.dedent(
        """
        app demo
          stack nextjs
          freedom guided

          entity Timesheet
            id: Id
            period_key: String
            status: String

          entity Invoice
            id: Id
            period_key: String
            status: String

          pattern auth
            provider: clerk
            roles: coordinator, payroll

          custom collect_open
            why "collect open rows"
            lines 4

          custom send_mail
            why "send after approval"
            lines 4
        """
    )
    return header + body + "\n"


def _diags(extra: str, registry=None):
    app = parse_app(_app(extra))
    return validate(app, registry or load_registry(DEFAULT_REGISTRY))


def _codes(diags) -> list[str]:
    return [item.code for item in diags]


_COLLECT = """
pattern ai_task Collect
  entity: Timesheet
  trigger: schedule
  schedule: 0 9 * * 1
  steps: collect_open
  effects: read, write
  audience: internal
  spend_per_run: 0
  spend_per_period: 0
  period: weekly
  net_per_hour: 0
  emit_per_hour: 0
  retries: 1
  escalate_to: coordinator
  idempotency_key: period_key
  audit: ast_hash
"""


class TestCronAndCounts(unittest.TestCase):
    def test_cron_accepts_five_numeric_fields(self) -> None:
        self.assertTrue(parse_cron("0 9 * * 1"))
        self.assertTrue(parse_cron("*/15 0 1 1 0"))
        self.assertTrue(parse_cron("0 9 1-5 * 1,3"))
        self.assertFalse(parse_cron("0 9 * * MON"))
        self.assertFalse(parse_cron("0 9 * *"))
        self.assertFalse(parse_cron("60 9 * * *"))
        self.assertFalse(parse_cron("0 24 * * *"))

    def test_counts_reject_signs_and_leading_zeros(self) -> None:
        self.assertEqual(parse_count("0"), 0)
        self.assertEqual(parse_count("20"), 20)
        self.assertIsNone(parse_count("00"))
        self.assertIsNone(parse_count("-1"))
        self.assertIsNone(parse_count("1.5"))


class TestAutomationExamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry(DEFAULT_REGISTRY)

    def test_examples_build_and_the_broken_one_is_caught(self) -> None:
        for name in ("ai_automation.prout", "ai_desk.prout"):
            app = parse_app((PATTERNS / "examples" / name).read_text(encoding="utf-8"))
            self.assertEqual(validate(app, self.registry), [], name)

        broken = parse_app((PATTERNS / "examples" / "ai_automation_broken.prout").read_text(encoding="utf-8"))
        diags = validate(broken, self.registry)
        self.assertEqual(_codes(diags), ["E-POLICY", "E-POLICY", "E-POLICY"])
        text = " ".join(item.message for item in diags)
        self.assertIn("emit to an external party has no approval gate", text)
        self.assertIn("per-run spend exceeds the period budget (5000 <= 1000 is false)", text)
        self.assertIn("a contract change has no approval gate", text)

    def test_existing_apps_still_validate(self) -> None:
        for path in (
            PATTERNS / "examples" / "shop.prout",
            PATTERNS / "examples" / "expense.prout",
            PATTERNS / "examples" / "staffing.prout",
            PATTERNS / "examples" / "staffing_desk.prout",
        ):
            app = parse_app(path.read_text(encoding="utf-8"))
            errors = [item for item in validate(app, self.registry) if item.severity == "error"]
            self.assertEqual(errors, [], path.name)

    def test_find_keeps_approval_and_discovers_the_new_patterns(self) -> None:
        found = find_patterns("申請 承認", self.registry)
        self.assertEqual(found[0][1].name, "approval")
        self.assertEqual(found[0][0], 15)
        self.assertEqual(find_patterns("自動化", self.registry)[0][1].name, "ai_task")
        self.assertEqual(find_patterns("承認ゲート", self.registry)[0][1].name, "approval_gate")

    def test_manifest_notes_keep_the_phase_one_limits(self) -> None:
        notes = " ".join(self.registry["ai_task"].notes)
        self.assertIn("prout.py", notes)
        self.assertIn("ast_hash", notes)
        self.assertIn("on_approved", notes)
        gate = " ".join(self.registry["approval_gate"].notes)
        self.assertIn("demo.html", gate)


class TestAutomationPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry(DEFAULT_REGISTRY)

    def test_internal_work_and_a_gated_external_emit_pass(self) -> None:
        diags = _diags(
            _join(
                _COLLECT,
                """
                pattern ai_task Remind
                  entity: Timesheet
                  trigger: schedule
                  schedule: 0 9 * * 1
                  steps: collect_open
                  effects: read, emit
                  audience: external
                  approval: RemindClient
                  spend_per_run: 0
                  spend_per_period: 0
                  period: weekly
                  net_per_hour: 0
                  emit_per_hour: 20
                  retries: 0
                  escalate_to: coordinator
                  idempotency_key: period_key
                  audit: ast_hash
                pattern approval_gate RemindClient
                  entity: Timesheet
                  approver: coordinator
                  action: emit
                  audience: external
                  on_approved: send_mail
                pattern ai_task CloseAndPay
                  entity: Invoice
                  trigger: schedule
                  schedule: 0 10 1 * *
                  steps: collect_open
                  effects: read, emit, spend
                  audience: external
                  approval: SendInvoice, PayInvoice
                  spend_per_run: 100
                  spend_per_period: 100
                  period: monthly
                  net_per_hour: 0
                  emit_per_hour: 5
                  retries: 0
                  escalate_to: payroll
                  idempotency_key: period_key
                  audit: ast_hash
                pattern approval_gate SendInvoice
                  entity: Invoice
                  approver: payroll
                  action: emit
                  audience: external
                pattern approval_gate PayInvoice
                  entity: Invoice
                  approver: payroll
                  action: spend
                  audience: external
                """,
            ),
            self.registry,
        )
        self.assertEqual(diags, [])

    def test_internal_emit_does_not_need_a_gate(self) -> None:
        diags = _diags(
            """
            pattern ai_task Flag
              entity: Timesheet
              trigger: schedule
              schedule: 0 8 * * *
              steps: collect_open
              effects: read, emit
              audience: internal
              spend_per_run: 0
              spend_per_period: 0
              period: daily
              net_per_hour: 0
              emit_per_hour: 5
              retries: 1
              escalate_to: coordinator
              idempotency_key: period_key
              audit: ast_hash
            """,
            self.registry,
        )
        self.assertEqual(diags, [])

    def test_external_net_and_spend_need_a_gate(self) -> None:
        net = _diags(
            """
            pattern ai_task Ping
              entity: Timesheet
              trigger: webhook
              webhook: inbound/inquiry
              steps: collect_open
              effects: read, net
              audience: external
              spend_per_run: 0
              spend_per_period: 0
              period: daily
              net_per_hour: 5
              emit_per_hour: 0
              retries: 1
              escalate_to: coordinator
              idempotency_key: period_key
              audit: ast_hash
            """,
            self.registry,
        )
        self.assertTrue(any("net to an external party has no approval gate" in item.message for item in net))

        spend = _diags(
            """
            pattern ai_task Pay
              entity: Invoice
              trigger: schedule
              schedule: 0 10 1 * *
              steps: collect_open
              effects: read, spend
              audience: external
              spend_per_run: 100
              spend_per_period: 100
              period: monthly
              net_per_hour: 0
              emit_per_hour: 0
              retries: 0
              escalate_to: payroll
              idempotency_key: period_key
              audit: ast_hash
            """,
            self.registry,
        )
        self.assertTrue(any("spend has no approval gate" in item.message for item in spend))

    def test_budgets_match_the_declared_effects(self) -> None:
        zero = _diags(
            """
            pattern ai_task Pay
              entity: Invoice
              trigger: schedule
              schedule: 0 10 1 * *
              steps: collect_open
              effects: read, spend
              audience: external
              approval: PayStaff
              spend_per_run: 0
              spend_per_period: 0
              period: monthly
              net_per_hour: 0
              emit_per_hour: 0
              retries: 0
              escalate_to: payroll
              idempotency_key: period_key
              audit: ast_hash
            pattern approval_gate PayStaff
              entity: Invoice
              approver: payroll
              action: spend
              audience: external
            """,
            self.registry,
        )
        self.assertTrue(any("positive per-run and per-period budget" in item.message for item in zero))

        unused = _diags(
            _COLLECT.replace("net_per_hour: 0", "net_per_hour: 4"),
            self.registry,
        )
        self.assertTrue(any("net rate limit is set but net is not an allowed effect" in item.message for item in unused))

    def test_trigger_slots_cron_steps_and_retries(self) -> None:
        missing = _diags(
            _COLLECT.replace("  schedule: 0 9 * * 1\n", ""),
            self.registry,
        )
        self.assertTrue(any("requires slot 'schedule'" in item.message for item in missing))

        extra = _diags(
            _COLLECT.replace("trigger: schedule", "trigger: record").replace(
                "schedule: 0 9 * * 1", "on_record: Timesheet\n  schedule: 0 9 * * 1"
            ),
            self.registry,
        )
        self.assertTrue(any("only valid when trigger is schedule" in item.message for item in extra))

        bad_cron = _diags(
            _COLLECT.replace("0 9 * * 1", "0 9 * * MON"),
            self.registry,
        )
        self.assertTrue(any(item.code == "E-SLOT" and "cron" in item.message for item in bad_cron))

        unknown_step = _diags(
            _COLLECT.replace("steps: collect_open", "steps: missing_step"),
            self.registry,
        )
        self.assertTrue(any(item.code == "E-HOOK" and "missing_step" in item.message for item in unknown_step))

        retries = _diags(_COLLECT.replace("retries: 1", "retries: 6"), self.registry)
        self.assertTrue(any("retries must be from 0 through 5" in item.message for item in retries))

    def test_roles_keys_audit_and_gate_shape(self) -> None:
        role = _diags(_COLLECT.replace("escalate_to: coordinator", "escalate_to: stranger"), self.registry)
        self.assertTrue(any("stranger" in item.message and "auth.roles" in item.message for item in role))

        missing_key = _diags(
            _COLLECT.replace("idempotency_key: period_key", "idempotency_key: missing"),
            self.registry,
        )
        self.assertTrue(any(item.code == "E-ENTITY" and "missing" in item.message for item in missing_key))

        audit = _diags(_COLLECT.replace("audit: ast_hash", "audit: sha256"), self.registry)
        self.assertTrue(any(item.code == "E-SLOT" and "ast_hash" in item.message for item in audit))

        unnamed = _diags(
            """
            pattern approval_gate
              entity: Timesheet
              approver: coordinator
              action: emit
              audience: external
            """,
            self.registry,
        )
        self.assertTrue(any("requires a name" in item.message for item in unnamed))

        mismatch = _diags(
            """
            pattern ai_task Remind
              entity: Timesheet
              trigger: schedule
              schedule: 0 9 * * 1
              steps: collect_open
              effects: read, emit
              audience: external
              approval: RemindClient
              spend_per_run: 0
              spend_per_period: 0
              period: weekly
              net_per_hour: 0
              emit_per_hour: 10
              retries: 0
              escalate_to: coordinator
              idempotency_key: period_key
              audit: ast_hash
            pattern approval_gate RemindClient
              entity: Invoice
              approver: coordinator
              action: spend
              audience: internal
            """,
            self.registry,
        )
        text = " ".join(item.message for item in mismatch)
        self.assertIn("is for entity 'Invoice', not 'Timesheet'", text)
        self.assertIn("action 'spend' is not one of the gated effects", text)
        self.assertIn("emit to an external party has no approval gate", text)

        unused_gate = _diags(
            _join(
                _COLLECT,
                """
                pattern ai_task CollectAgain
                  entity: Timesheet
                  trigger: schedule
                  schedule: 0 9 * * 2
                  steps: collect_open
                  effects: read
                  audience: internal
                  approval: RemindClient
                  spend_per_run: 0
                  spend_per_period: 0
                  period: weekly
                  net_per_hour: 0
                  emit_per_hour: 0
                  retries: 0
                  escalate_to: coordinator
                  idempotency_key: period_key
                  audit: ast_hash
                pattern approval_gate RemindClient
                  entity: Timesheet
                  approver: coordinator
                  action: emit
                  audience: external
                """,
            ),
            self.registry,
        )
        self.assertTrue(any("no gated effect" in item.message for item in unused_gate))

    def test_duplicate_gate_names_are_rejected(self) -> None:
        diags = _diags(
            """
            pattern approval_gate RemindClient
              entity: Timesheet
              approver: coordinator
              action: emit
              audience: external
            pattern approval_gate RemindClient
              entity: Timesheet
              approver: payroll
              action: emit
              audience: external
            """,
            self.registry,
        )
        self.assertIn("E-DUP", _codes(diags))

    def test_contract_layer_rejects_task_declarations(self) -> None:
        with self.assertRaises(ProutError) as caught:
            Parser(snippet("task collect(period: Id) -> Int\n  return 1")).parse()
        self.assertIn("unknown declaration", str(caught.exception))

    def test_automation_manifest_shape_is_checked(self) -> None:
        data = json.loads((PATTERNS / "registry" / "ai_task.json").read_text(encoding="utf-8"))
        data["automation"]["retries_max"] = -1
        errors = validate_manifest(manifest_from_dict(data))
        self.assertTrue(any("retries_max" in item for item in errors))

        data = json.loads((PATTERNS / "registry" / "ai_task.json").read_text(encoding="utf-8"))
        data["automation"]["gates"][0]["kind"] = "mail"
        errors = validate_manifest(manifest_from_dict(data))
        self.assertTrue(any("not an effect" in item for item in errors))
