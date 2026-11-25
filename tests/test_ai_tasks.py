"""Contract-layer agent and task checks.

Phase 1 is static. These tests do not run cron, count budgets, or store
idempotency keys. Spend amounts are integer yen.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import support  # noqa: F401
from prout import Checker, Parser, ProutError, ast_digest, ast_hash, program_to_dict, task_plan
from support import check, codes, parse, snippet

ROOT = Path(__file__).resolve().parents[1]

# Hashes captured before agent/task nodes were added to the canonical AST.
# Programs that do not declare them must keep these prefixes.
SAMPLE_HASHES = {
    "refund_broken.prout": "c0ee67d7cba1",
    "refund_fixed.prout": "bae8501e8b3f",
    "staffing.prout": "afd29e7ccdd0",
    "staffing_broken.prout": "afbf07d01bd2",
    "staffing_dates.prout": "ecc914507aae",
    "staffing_dates_broken.prout": "5a9f7cb9dd46",
}

HEADER = """
    type Id
    type Invoice {
      id: Id
    }
    type Timesheet

    op timesheets.open(period: Id) -> Timesheet
      doc "read"
      eff read(timesheets)
      impl native

    op mail.send(sheet: Timesheet) -> Bool
      doc "send"
      eff emit(mail)
      impl native

    op flags.raise(period: Id) -> Bool
      doc "internal flag"
      eff emit(queue)
      impl native

    op invoices.draft(period: Id) -> Invoice
      doc "draft"
      eff read(timesheets), write(invoices)
      impl native

    op payroll.transfer(invoice: Invoice) -> Bool
      doc "pay"
      eff spend(payroll)
      impl native

    op contracts.extend(period: Id) -> Bool
      doc "extend"
      eff read(contracts), write(contracts)
      impl native

    op billing.post(period: Id) -> Bool
      doc "post"
      eff net(stripe)
      impl native

    op ping_customer(period: Id) -> Bool
      doc "customer mail"
      eff emit(mail)
      impl native

    policy
      external emit(mail), net(stripe)
      payment spend(payroll)
      contract write(contracts)
"""


def program(extra: str):
    return parse(snippet(HEADER) + snippet(extra))


def diags(extra: str):
    return check(snippet(HEADER) + snippet(extra))


class ParseAgentTask(unittest.TestCase):
    def test_unknown_declaration_is_still_rejected(self) -> None:
        with self.assertRaises(ProutError) as caught:
            Parser(snippet("widget nope\n")).parse()
        self.assertIn("unknown declaration", str(caught.exception))

    def test_decimal_budget_is_rejected(self) -> None:
        with self.assertRaises(ProutError) as caught:
            program(
                """
                agent clerk
                  allow read(timesheets)
                  budget run 1.5 period 2
                  audit ast_hash
                """
            )
        self.assertIn("integer number of yen", str(caught.exception))

    def test_schedule_record_and_webhook_shapes(self) -> None:
        parsed = program(
            """
            agent clerk
              allow read(timesheets), write(invoices)
              budget run 0 period 0
              limit net 0 hour
              limit emit 0 hour
              audit ast_hash
            task by_cron(period: Id) -> Invoice
              agent clerk
              trigger schedule "*/15 8-18 1,15 * 1-5"
              eff read(timesheets), write(invoices)
              key period
              retry 0 escalate coordinator
              let draft = invoices.draft(period)
              return draft
            task by_row(period: Id) -> Invoice
              agent clerk
              trigger record Invoice
              eff read(timesheets), write(invoices)
              key period
              retry 0 escalate coordinator
              let draft = invoices.draft(period)
              return draft
            task by_hook(period: Id) -> Invoice
              agent clerk
              trigger webhook "inbound/inquiry"
              eff read(timesheets), write(invoices)
              key period
              retry 5 escalate coordinator
              let draft = invoices.draft(period)
              return draft
            """
        )
        self.assertEqual([task.trigger_kind for task in parsed.tasks], ["schedule", "record", "webhook"])
        self.assertEqual(codes(Checker(parsed).check(), "error"), [])

    def test_record_trigger_is_insert_only_in_the_ast(self) -> None:
        parsed = program(
            """
            agent clerk
              allow read(timesheets)
              budget run 0 period 0
              limit net 0 hour
              limit emit 0 hour
              audit ast_hash
            task on_new(invoice: Invoice) -> Id
              agent clerk
              trigger record Invoice
              eff read(timesheets)
              key invoice.id
              retry 0 escalate coordinator
              let sheet = timesheets.open(invoice.id)
              return invoice.id
            """
        )
        data = program_to_dict(parsed)
        trigger = next(item for item in data["tasks"] if item["name"] == "on_new")["trigger"]
        self.assertEqual(trigger, {"kind": "record", "name": "Invoice", "on": "insert"})
        self.assertEqual(data["agents"][0]["timezone"], "Asia/Tokyo")

    def test_webhook_keeps_only_the_receiver_name(self) -> None:
        parsed = program(
            """
            agent clerk
              allow read(timesheets)
              budget run 0 period 0
              limit net 0 hour
              limit emit 0 hour
              audit ast_hash
            task inbound(period: Id) -> Timesheet
              agent clerk
              trigger webhook "inbound/inquiry"
              eff read(timesheets)
              key period
              retry 0 escalate coordinator
              let sheet = timesheets.open(period)
              return sheet
            """
        )
        trigger = program_to_dict(parsed)["tasks"][0]["trigger"]
        self.assertEqual(trigger, {"kind": "webhook", "name": "inbound/inquiry"})
        self.assertNotIn("signature", trigger)

    def test_named_weekday_is_not_a_schedule(self) -> None:
        found = diags(
            """
            agent clerk
              allow read(timesheets)
              budget run 0 period 0
              limit net 0 hour
              limit emit 0 hour
              audit ast_hash
            task weekly(period: Id) -> Timesheet
              agent clerk
              trigger schedule "0 9 * * MON"
              eff read(timesheets)
              key period
              retry 0 escalate coordinator
              let sheet = timesheets.open(period)
              return sheet
            """
        )
        self.assertTrue(any("MON" in item.message for item in found))
        self.assertIn("E-POLICY", codes(found, "error"))


class PolicyChecks(unittest.TestCase):
    def _mail_agent(self, allow: str, emit: int = 20, net: int = 0, run: int = 0, period: int = 0) -> str:
        return f"""
            agent clerk
              allow {allow}
              budget run {run} period {period}
              limit net {net} hour
              limit emit {emit} hour
              audit ast_hash
        """

    def test_external_emit_without_a_gate_is_e_policy(self) -> None:
        found = diags(
            self._mail_agent("read(timesheets), emit(mail)")
            + """
            task remind(period: Id) -> Bool
              agent clerk
              trigger schedule "0 9 * * 1"
              eff read(timesheets), emit(mail)
              key period
              retry 1 escalate coordinator
              let sheet = timesheets.open(period)
              let sent = mail.send(sheet)
              return sent
            """
        )
        self.assertTrue(any("emit to an external party has no approval gate" in item.message for item in found))

    def test_external_net_without_a_gate_is_e_policy(self) -> None:
        found = diags(
            self._mail_agent("read(timesheets), net(stripe)", emit=0, net=3)
            + """
            task post(period: Id) -> Bool
              agent clerk
              trigger schedule "0 9 * * 1"
              eff net(stripe)
              key period
              retry 1 escalate coordinator
              let posted = billing.post(period)
              return posted
            """
        )
        self.assertTrue(any("net to an external party has no approval gate" in item.message for item in found))

    def test_internal_emit_does_not_need_a_gate(self) -> None:
        found = diags(
            self._mail_agent("read(timesheets), emit(queue)")
            + """
            task flag(period: Id) -> Bool
              agent clerk
              trigger schedule "0 9 * * 1"
              eff read(timesheets), emit(queue)
              key period
              retry 1 escalate coordinator
              let sheet = timesheets.open(period)
              let raised = flags.raise(period)
              return raised
            """
        )
        self.assertEqual(codes(found, "error"), [])

    def test_spend_and_contract_writes_need_gates(self) -> None:
        spend = diags(
            self._mail_agent("read(timesheets), spend(payroll)", emit=0, run=100, period=100)
            + """
            task pay(invoice: Invoice) -> Bool
              agent clerk
              trigger schedule "0 10 1 * *"
              eff spend(payroll)
              key invoice.id
              retry 0 escalate payroll
              let paid = payroll.transfer(invoice)
              return paid
            """
        )
        self.assertTrue(any("spend has no approval gate" in item.message for item in spend))
        change = diags(
            self._mail_agent("read(contracts), write(contracts)", emit=0)
            + """
            task extend(period: Id) -> Bool
              agent clerk
              trigger schedule "0 11 * * 1"
              eff read(contracts), write(contracts)
              key period
              retry 0 escalate coordinator
              let ok = contracts.extend(period)
              return ok
            """
        )
        self.assertTrue(any("a contract change has no approval gate" in item.message for item in change))

    def test_a_matching_gate_lets_spend_and_external_emit_through(self) -> None:
        found = diags(
            self._mail_agent("read(timesheets), emit(mail), spend(payroll)", run=100, period=1000)
            + """
            gate remind_client
              approver coordinator
              before emit(mail)
              on_approved mail.send
            gate pay_staff
              approver payroll
              before spend(payroll)
              on_approved payroll.transfer
            task remind(period: Id) -> Bool
              agent clerk
              trigger schedule "0 9 * * 1"
              eff read(timesheets), emit(mail)
              gate remind_client
              key period
              retry 2 escalate coordinator
              let sheet = timesheets.open(period)
              let sent = mail.send(sheet)
              return sent
            task pay(invoice: Invoice) -> Bool
              agent clerk
              trigger schedule "0 10 1 * *"
              eff spend(payroll)
              gate pay_staff
              budget run 100 period 1000
              key invoice.id
              retry 0 escalate payroll
              let paid = payroll.transfer(invoice)
              return paid
            """
        )
        self.assertEqual(codes(found, "error"), [])

    def test_per_run_spend_cannot_exceed_the_period(self) -> None:
        found = diags(
            self._mail_agent("spend(payroll)", emit=0, run=5000, period=5000)
            + """
            gate pay_staff
              approver payroll
              before spend(payroll)
              on_approved payroll.transfer
            task pay(invoice: Invoice) -> Bool
              agent clerk
              trigger schedule "0 10 1 * *"
              eff spend(payroll)
              gate pay_staff
              budget run 5000 period 1000
              key invoice.id
              retry 0 escalate payroll
              let paid = payroll.transfer(invoice)
              return paid
            """
        )
        self.assertTrue(any("5000 <= 1000 is false" in item.message for item in found))
        self.assertEqual(codes(found, "error"), ["E-POLICY"])

    def test_agent_spend_budget_must_be_positive_and_ordered(self) -> None:
        zero = diags(
            """
            agent clerk
              allow spend(payroll)
              budget run 0 period 0
              limit net 0 hour
              limit emit 0 hour
              audit ast_hash
            """
        )
        self.assertTrue(any("positive per-run and per-period budget" in item.message for item in zero))
        backwards = diags(
            """
            agent clerk
              allow spend(payroll)
              budget run 9 period 4
              limit net 0 hour
              limit emit 0 hour
              audit ast_hash
            """
        )
        self.assertTrue(any("9 <= 4 is false" in item.message for item in backwards))
        unused = diags(
            """
            agent clerk
              allow read(timesheets)
              budget run 10 period 10
              limit net 0 hour
              limit emit 0 hour
              audit ast_hash
            """
        )
        self.assertTrue(any("spend is not an allowed effect" in item.message for item in unused))

    def test_hourly_limits_follow_the_allow_list(self) -> None:
        missing = diags(
            """
            agent clerk
              allow emit(mail)
              budget run 0 period 0
              limit net 0 hour
              limit emit 0 hour
              audit ast_hash
            """
        )
        self.assertTrue(any("emit effect needs a positive hourly rate limit" in item.message for item in missing))
        extra = diags(
            """
            agent clerk
              allow read(timesheets)
              budget run 0 period 0
              limit net 4 hour
              limit emit 0 hour
              audit ast_hash
            """
        )
        self.assertTrue(any("net is not an allowed effect" in item.message for item in extra))

    def test_retry_must_be_from_0_through_5(self) -> None:
        found = diags(
            self._mail_agent("read(timesheets)", emit=0)
            + """
            task once(period: Id) -> Timesheet
              agent clerk
              trigger schedule "0 9 * * 1"
              eff read(timesheets)
              key period
              retry 6 escalate coordinator
              let sheet = timesheets.open(period)
              return sheet
            """
        )
        self.assertTrue(any("retries must be from 0 through 5, got 6" in item.message for item in found))
        ok = diags(
            self._mail_agent("read(timesheets)", emit=0)
            + """
            task once(period: Id) -> Timesheet
              agent clerk
              trigger schedule "0 9 * * 1"
              eff read(timesheets)
              key period
              retry 5 escalate coordinator
              let sheet = timesheets.open(period)
              return sheet
            """
        )
        self.assertEqual(codes(ok, "error"), [])

    def test_escalation_must_not_produce_external_effects(self) -> None:
        found = diags(
            self._mail_agent("read(timesheets)", emit=0)
            + """
            task once(period: Id) -> Timesheet
              agent clerk
              trigger schedule "0 9 * * 1"
              eff read(timesheets)
              key period
              retry 1 escalate ping_customer
              let sheet = timesheets.open(period)
              return sheet
            """
        )
        self.assertTrue(any("escalation path produces external effects" in item.message for item in found))
        self.assertIn("E-POLICY", codes(found, "error"))

    def test_effects_must_stay_inside_the_agent_allow_list(self) -> None:
        found = diags(
            self._mail_agent("read(timesheets)", emit=0)
            + """
            task draft(period: Id) -> Invoice
              agent clerk
              trigger schedule "0 9 1 * *"
              eff read(timesheets), write(invoices)
              key period
              retry 0 escalate coordinator
              let draft = invoices.draft(period)
              return draft
            """
        )
        self.assertTrue(any("outside agent clerk allow" in item.message and "write(invoices)" in item.message for item in found))

    def test_declared_effects_must_match_called_ops(self) -> None:
        found = diags(
            self._mail_agent("read(timesheets), write(invoices)", emit=0)
            + """
            task draft(period: Id) -> Invoice
              agent clerk
              trigger schedule "0 9 1 * *"
              eff read(timesheets)
              key period
              retry 0 escalate coordinator
              let draft = invoices.draft(period)
              return draft
            """
        )
        self.assertIn("E-EFF", codes(found, "error"))

    def test_unknown_agent_and_unknown_op_are_errors(self) -> None:
        found = diags(
            """
            task draft(period: Id) -> Invoice
              agent missing_clerk
              trigger schedule "0 9 1 * *"
              eff read(timesheets)
              key period
              retry 0 escalate coordinator
              let draft = invoices.missing(period)
              return draft
            """
        )
        messages = " ".join(item.message for item in found)
        self.assertIn("unknown agent", messages)
        self.assertIn("unknown operation", messages)
        self.assertTrue({"E-UNDEF"} <= set(codes(found, "error")))

    def test_unknown_gate_and_agent_approver_are_rejected(self) -> None:
        missing = diags(
            self._mail_agent("read(timesheets), emit(mail)")
            + """
            task remind(period: Id) -> Bool
              agent clerk
              trigger schedule "0 9 * * 1"
              eff read(timesheets), emit(mail)
              gate missing_gate
              key period
              retry 1 escalate coordinator
              let sheet = timesheets.open(period)
              let sent = mail.send(sheet)
              return sent
            """
        )
        self.assertTrue(any("is not declared" in item.message for item in missing))
        agent_approver = diags(
            self._mail_agent("read(timesheets)", emit=0)
            + """
            gate remind_client
              approver clerk
              before read(timesheets)
            task read_only(period: Id) -> Timesheet
              agent clerk
              trigger schedule "0 9 * * 1"
              eff read(timesheets)
              key period
              retry 0 escalate coordinator
              let sheet = timesheets.open(period)
              return sheet
            """
        )
        self.assertTrue(any("approver 'clerk' is an agent" in item.message for item in agent_approver))

    def test_a_gate_on_an_ungated_task_is_rejected(self) -> None:
        found = diags(
            self._mail_agent("read(timesheets)", emit=0)
            + """
            gate remind_client
              approver coordinator
              before read(timesheets)
            task read_only(period: Id) -> Timesheet
              agent clerk
              trigger schedule "0 9 * * 1"
              eff read(timesheets)
              gate remind_client
              key period
              retry 0 escalate coordinator
              let sheet = timesheets.open(period)
              return sheet
            """
        )
        self.assertTrue(any("no gated effect" in item.message for item in found))

    def test_idempotency_key_must_name_a_parameter_or_its_field(self) -> None:
        found = diags(
            self._mail_agent("read(timesheets)", emit=0)
            + """
            task read_only(period: Id) -> Timesheet
              agent clerk
              trigger schedule "0 9 * * 1"
              eff read(timesheets)
              key other
              retry 0 escalate coordinator
              let sheet = timesheets.open(period)
              return sheet
            """
        )
        self.assertTrue(any("idempotency key" in item.message for item in found))

    def test_audit_ast_hash_is_required(self) -> None:
        found = diags(
            """
            agent clerk
              allow read(timesheets)
              budget run 0 period 0
              limit net 0 hour
              limit emit 0 hour
              audit sha256
            """
        )
        self.assertTrue(any("audit ast_hash is required" in item.message for item in found))


class BothPolicies(unittest.TestCase):
    def test_information_flow_and_task_policy_in_one_program(self) -> None:
        text = """
            label pii

            type Id
            type Customer {
              id: Id
              email: String @pii
            }

            policy
              sink net(email) accepts pii

            policy
              external net(email)

            op customers.get(id: Id) -> Customer
              doc "load"
              eff read(customers)
              impl native

            op send_receipt(to: String @pii) -> Bool
              doc "mail"
              eff net(email)
              sends to -> net(email)
              impl native

            agent mailer
              doc "sends an approved receipt"
              allow read(customers), net(email)
              budget run 0 period 0
              limit net 5 hour
              limit emit 0 hour
              audit ast_hash

            gate send_mail
              doc "a person approves the mail"
              approver support
              before net(email)
              on_approved send_receipt

            flow email_receipt(id: Id) -> Bool
              doc "send the receipt"
              eff read(customers), net(email)
              let customer = customers.get(id)
              return send_receipt(customer.email)

            task remind(period: Id) -> Bool
              agent mailer
              doc "send a receipt after approval"
              trigger schedule "0 9 * * 1"
              eff read(customers), net(email)
              gate send_mail
              key period
              retry 1 escalate support
              let customer = customers.get(period)
              let sent = send_receipt(customer.email)
              return sent
        """
        found = check(text, allow_net=["email", "stripe"])
        self.assertEqual(codes(found, "error"), [], [item.message for item in found])
        program = parse(text)
        self.assertEqual([item.kind for item in program.policies], ["flow", "task"])
        data = program_to_dict(program)
        self.assertEqual(data["policy"]["sinks"][0]["effect"], ["net", "email"])
        self.assertEqual(data["policies"][0]["kind"], "task")
        self.assertEqual(data["policies"][0]["external"], [("net", "email")])
        self.assertIn("pii", [item["name"] for item in data["labels"]])
        self.assertIn("remind", [item["name"] for item in data["tasks"]])
        self.assertIn("email_receipt", [item["name"] for item in data["flows"]])


class AstHash(unittest.TestCase):
    def test_existing_samples_keep_their_hashes(self) -> None:
        for name, digest in SAMPLE_HASHES.items():
            program = Parser((ROOT / "examples" / name).read_text(encoding="utf-8")).parse()
            data = program_to_dict(program)
            self.assertNotIn("agents", data, name)
            self.assertNotIn("tasks", data, name)
            self.assertNotIn("gates", data, name)
            self.assertNotIn("policies", data, name)
            self.assertEqual(ast_hash(program), digest, name)
            full = ast_digest(program)
            self.assertEqual(len(full), 64, name)
            self.assertTrue(full.startswith(digest), name)

    def test_agents_and_tasks_change_the_hash_and_ignore_order(self) -> None:
        first = program(
            """
            agent beta
              doc "b"
              allow read(timesheets)
              budget run 0 period 0
              limit net 0 hour
              limit emit 0 hour
              audit ast_hash
            agent alpha
              doc "a"
              allow read(timesheets)
              budget run 0 period 0
              limit net 0 hour
              limit emit 0 hour
              audit ast_hash
            task second(period: Id) -> Timesheet
              agent alpha
              doc "two"
              trigger schedule "0 9 * * 1"
              eff read(timesheets)
              key period
              retry 0 escalate coordinator
              let sheet = timesheets.open(period)
              return sheet
            task first(period: Id) -> Timesheet
              agent beta
              doc "one"
              trigger schedule "0 8 * * *"
              eff read(timesheets)
              key period
              retry 1 escalate coordinator
              let sheet = timesheets.open(period)
              return sheet
            """
        )
        reordered = program(
            """
            agent alpha
              allow read(timesheets)
              doc "a"
              limit emit 0 hour
              budget run 0 period 0
              limit net 0 hour
              audit ast_hash
            agent beta
              doc "b"
              budget run 0 period 0
              allow read(timesheets)
              limit net 0 hour
              limit emit 0 hour
              audit ast_hash
            task first(period: Id) -> Timesheet
              agent beta
              trigger schedule "0 8 * * *"
              doc "one"
              key period
              eff read(timesheets)
              retry 1 escalate coordinator
              let sheet = timesheets.open(period)
              return sheet
            task second(period: Id) -> Timesheet
              agent alpha
              eff read(timesheets)
              trigger schedule "0 9 * * 1"
              doc "two"
              retry 0 escalate coordinator
              key period
              let sheet = timesheets.open(period)
              return sheet
            """
        )
        self.assertEqual(ast_hash(first), ast_hash(reordered))
        changed = program(
            """
            agent alpha
              doc "a"
              allow read(timesheets)
              budget run 0 period 0
              limit net 0 hour
              limit emit 0 hour
              audit ast_hash
            agent beta
              doc "changed"
              allow read(timesheets)
              budget run 0 period 0
              limit net 0 hour
              limit emit 0 hour
              audit ast_hash
            task first(period: Id) -> Timesheet
              agent beta
              doc "one"
              trigger schedule "0 8 * * *"
              eff read(timesheets)
              key period
              retry 1 escalate coordinator
              let sheet = timesheets.open(period)
              return sheet
            task second(period: Id) -> Timesheet
              agent alpha
              doc "two"
              trigger schedule "0 9 * * 1"
              eff read(timesheets)
              key period
              retry 0 escalate coordinator
              let sheet = timesheets.open(period)
              return sheet
            """
        )
        self.assertNotEqual(ast_hash(first), ast_hash(changed))
        data = program_to_dict(first)
        self.assertEqual([item["name"] for item in data["agents"]], ["alpha", "beta"])
        self.assertEqual([item["name"] for item in data["tasks"]], ["first", "second"])
        self.assertEqual(data["tasks"][0]["key"], "period")
        self.assertNotIn(ast_digest(first), str(data["tasks"][0]["key"]))

    def test_examples_and_the_check_plan(self) -> None:
        good = Parser((ROOT / "examples" / "ai_tasks.prout").read_text(encoding="utf-8")).parse()
        self.assertEqual(codes(check(good.source), "error"), [])
        plan = "\n".join(task_plan(good))
        self.assertIn("remind_timesheet", plan)
        self.assertIn("抵触日が近い契約を社内キューに載せる", plan)
        self.assertIn("請求の下書きを作り、送付には承認ゲートを宣言する", plan)
        self.assertIn("send_invoice", plan)
        self.assertIn("remind_return", plan)
        self.assertIn("Asia/Tokyo", plan)
        self.assertIn(ast_digest(good), plan)
        broken = check((ROOT / "examples" / "ai_tasks_broken.prout").read_text(encoding="utf-8"))
        errors = [item for item in broken if item.severity == "error"]
        self.assertEqual([item.code for item in errors], ["E-POLICY"] * 7)
        text = "\n".join(item.message for item in errors)
        self.assertIn("emit to an external party has no approval gate", text)
        self.assertIn("net to an external party has no approval gate", text)
        self.assertIn("spend has no approval gate", text)
        self.assertIn("a contract change has no approval gate", text)
        self.assertIn("5000 <= 1000 is false", text)
        self.assertIn("retries must be from 0 through 5, got 6", text)
        self.assertIn("escalation path produces external effects", text)


if __name__ == "__main__":
    unittest.main()
