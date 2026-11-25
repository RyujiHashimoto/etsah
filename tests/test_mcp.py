"""MCP server protocol tests and the decisions behind the tools.

The protocol cases spawn ``etsah mcp``. Decision cases call the same functions
the server calls, so a verdict does not depend on the transport.
"""
from __future__ import annotations

import io
import json
import select
import subprocess
import sys
import unittest
from pathlib import Path

import support  # noqa: F401
from etsah_api import check_file
from etsah_mcp.decide import ALLOW, ASK, BLOCK, check_plan, check_report, decide_call, load_contract
from etsah_mcp.explain import explain_code
from etsah_mcp.framing import read_message, write_message
from etsah_mcp.imports import import_definitions

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "examples" / "agents"


def _read_contract(name: str) -> str:
    return (AGENTS / name).read_text(encoding="utf-8")


class RpcProcess:
    def __init__(self, argv: list[str], env: dict[str, str] | None = None):
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
            env=env,
        )
        self._next = 1

    def send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 5) -> dict:
        msg_id = self._next
        self._next += 1
        payload: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.send(payload)
        reply = self.read(timeout)
        if reply.get("id") != msg_id:
            raise AssertionError(f"expected id {msg_id}, got {reply}")
        return reply

    def read(self, timeout: float = 5) -> dict:
        assert self.proc.stdout is not None
        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not ready:
            err = b""
            if self.proc.stderr is not None:
                ready_err, _, _ = select.select([self.proc.stderr], [], [], 0.1)
                if ready_err:
                    err = self.proc.stderr.read1(8000)
            raise AssertionError(f"no MCP response within {timeout}s; stderr={err!r}")
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("MCP server closed stdout")
        return json.loads(line.decode("utf-8"))

    def close(self) -> bytes:
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2)
        err = b""
        if self.proc.stderr is not None:
            err = self.proc.stderr.read()
            self.proc.stderr.close()
        if self.proc.stdout is not None and not self.proc.stdout.closed:
            self.proc.stdout.close()
        return err


def _tool_payload(reply: dict) -> dict:
    result = reply["result"]
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])


TWO_AGENTS = """
type Id

op tickets.read(ticket_id: Id) -> Id
  doc "Read a fictional ticket"
  eff read(tickets)
  impl native

op mail.send_reply(ticket_id: Id) -> Bool
  doc "Send a fictional reply"
  eff emit(mail)
  impl native

op mail.blast(ticket_id: Id) -> Bool
  doc "Unused external send"
  eff emit(mail)
  impl native

op ledger.draft(ticket_id: Id) -> Id
  doc "Draft a fictional invoice"
  eff write(invoices)
  impl native

op ping_host(ticket_id: Id) -> Bool
  doc "Call the default stripe host. No task calls this."
  eff net(stripe)
  impl native

op payroll.transfer(ticket_id: Id) -> Bool
  doc "Unused payment. No task names a spend gate."
  eff spend(payroll)
  impl native

policy
  external emit(mail)

agent support_reply
  doc "Reads tickets and sends mail after approval"
  allow read(tickets), emit(mail)
  budget run 0 period 0
  limit net 0 hour
  limit emit 20 hour
  audit ast_hash

agent invoice_clerk
  doc "Writes invoices inside the desk"
  allow write(invoices)
  budget run 0 period 0
  limit net 0 hour
  limit emit 0 hour
  audit ast_hash

gate approve_reply
  doc "A support lead approves the reply"
  approver support_lead
  before emit(mail)
  on_approved mail.send_reply

task draft_reply(ticket_id: Id) -> Bool
  agent support_reply
  doc "Read a ticket and send the reply through the gate"
  trigger webhook "support-tickets"
  eff read(tickets), emit(mail)
  gate approve_reply
  key ticket_id
  retry 1 escalate support_lead
  let ticket = tickets.read(ticket_id)
  let sent = mail.send_reply(ticket)
  return sent

task draft_invoice(ticket_id: Id) -> Id
  agent invoice_clerk
  doc "Write an internal invoice draft"
  trigger webhook "invoices"
  eff write(invoices)
  key ticket_id
  retry 0 escalate billing_lead
  let draft = ledger.draft(ticket_id)
  return draft
"""


class DecisionTests(unittest.TestCase):
    def test_task_verdicts_on_the_shipped_contracts(self) -> None:
        reply = check_report(_read_contract("support_reply.etsah"))
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["plans"][0]["verdict"], ASK)
        self.assertIn("support_lead", reply["plans"][0]["reason"])
        self.assertEqual(reply["plans"][0]["name"], "draft_reply")
        self.assertTrue(reply["ast_hash"])
        self.assertEqual(len(reply["audit_id"]), 64)

        broken = check_report(_read_contract("support_reply_broken.etsah"))
        self.assertFalse(broken["ok"])
        self.assertEqual(broken["plans"][0]["verdict"], BLOCK)
        self.assertIn("emit to an external party has no approval gate", broken["plans"][0]["reason"])
        self.assertEqual(broken["diagnostics"][0]["code"], "E-POLICY")
        self.assertGreater(broken["diagnostics"][0]["line"], 0)
        self.assertIn("message", broken["diagnostics"][0])

        triage = check_report(_read_contract("github_triage.etsah"), allow_net=["github"])
        self.assertTrue(triage["ok"])
        self.assertEqual(triage["plans"][0]["verdict"], ALLOW)

        denied = check_report(_read_contract("github_triage.etsah"))
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["plans"][0]["verdict"], BLOCK)
        self.assertIn("github", denied["plans"][0]["reason"])

        invoice = load_contract(_read_contract("invoice.etsah"))
        self.assertTrue(invoice.ok)
        self.assertEqual(decide_call(invoice, "ledger.draft").verdict, ALLOW)
        self.assertEqual(decide_call(invoice, "payments.charge").verdict, ASK)
        self.assertIn("billing_lead", decide_call(invoice, "payments.charge").reason)

        cancel = load_contract(_read_contract("subscription_cancel.etsah"))
        self.assertTrue(cancel.ok)
        self.assertEqual(decide_call(cancel, "subscriptions.lookup").verdict, ALLOW)
        blocked_change = decide_call(cancel, "subscriptions.cancel")
        self.assertEqual(blocked_change.verdict, ASK)
        self.assertIn("account_owner", blocked_change.reason)

    def test_a_clean_file_can_mix_allow_ask_and_block(self) -> None:
        report = check_report(TWO_AGENTS)
        self.assertTrue(report["ok"], report["diagnostics"])
        by_name = {plan["name"]: plan["verdict"] for plan in report["plans"]}
        self.assertEqual(by_name, {"draft_reply": ASK, "draft_invoice": ALLOW})
        loaded = load_contract(TWO_AGENTS)
        self.assertEqual(decide_call(loaded, "tickets.read").verdict, ALLOW)
        self.assertEqual(decide_call(loaded, "mail.send_reply").verdict, ASK)
        self.assertEqual(decide_call(loaded, "ledger.draft").verdict, ALLOW)
        blast = decide_call(loaded, "mail.blast")
        self.assertEqual(blast.verdict, BLOCK)
        self.assertIn("more than one agent", blast.reason)
        blast_for_support = decide_call(loaded, "mail.blast", agent="support_reply")
        self.assertEqual(blast_for_support.verdict, ASK)
        self.assertIn("support_lead", blast_for_support.reason)
        unpaid = decide_call(loaded, "payroll.transfer", agent="invoice_clerk")
        self.assertEqual(unpaid.verdict, BLOCK)
        self.assertIn("spend has no approval gate", unpaid.reason)
        ambiguous = decide_call(loaded, "ping_host")
        self.assertEqual(ambiguous.verdict, BLOCK)
        self.assertIn("more than one agent", ambiguous.reason)
        outside = decide_call(loaded, "mail.send_reply", agent="invoice_clerk")
        self.assertEqual(outside.verdict, BLOCK)
        self.assertIn("outside agent invoice_clerk allow", outside.reason)
        unknown = decide_call(loaded, "no.such.tool")
        self.assertEqual(unknown.verdict, BLOCK)
        self.assertIn("not declared", unknown.reason)

    def test_a_broken_contract_blocks_every_step(self) -> None:
        plan = check_plan(
            _read_contract("support_reply_broken.etsah"),
            [{"tool": "tickets.read"}, {"name": "mail.send_reply"}],
        )
        self.assertFalse(plan["ok"])
        self.assertEqual([step["verdict"] for step in plan["steps"]], [BLOCK, BLOCK])
        self.assertIn("contract check failed", plan["steps"][0]["reason"])

    def test_parse_error_has_no_plan_and_blocks_the_step(self) -> None:
        report = check_report("op nope\n")
        self.assertFalse(report["ok"])
        self.assertEqual(report["plans"], [])
        self.assertEqual(report["ast_hash"], "")
        self.assertEqual(report["diagnostics"][0]["code"], "E-PARSE")
        plan = check_plan("op nope\n", [{"tool": "tickets.read"}])
        self.assertEqual(plan["steps"][0]["verdict"], BLOCK)
        self.assertIn("bad op header", plan["steps"][0]["reason"])

    def test_deny_net_blocks_the_default_host(self) -> None:
        source = 'op ping() -> Bool\n  eff net(stripe)\n  impl native\n'
        report = check_report(source, deny_net=["stripe"])
        self.assertFalse(report["ok"])
        self.assertIn("denied", report["diagnostics"][0]["message"])

    def test_explain_known_and_unknown_codes(self) -> None:
        policy = explain_code("E-POLICY")
        self.assertTrue(policy["known"])
        self.assertIn("gate", policy["explanation"])
        self.assertTrue(policy["suggested_fix"])
        folded = explain_code("  e-policy ")
        self.assertEqual(folded["code"], "E-POLICY")
        self.assertTrue(folded["known"])
        missing = explain_code("E-NOT-A-CODE")
        self.assertFalse(missing["known"])
        self.assertIn("No explanation", missing["explanation"])

    def test_unknown_side_effects_default_to_every_effect(self) -> None:
        sample = json.loads((AGENTS / "tools" / "mcp_tools.json").read_text(encoding="utf-8"))
        imported = import_definitions(sample, "mcp")
        by_name = {op["name"]: op for op in imported["ops"]}
        forecast = by_name["get_forecast"]
        self.assertEqual(
            forecast["effects"],
            [
                "read(get_forecast)",
                "write(get_forecast)",
                "net(get_forecast)",
                "spend(get_forecast)",
                "emit(get_forecast)",
            ],
        )
        self.assertTrue(any(flag.startswith("needs review:") for flag in forecast["review"]))
        self.assertEqual(by_name["send_email"]["effects"], ["net(send_email)", "emit(mail)"])
        self.assertEqual(by_name["list_tickets"]["effects"], ["read(list_tickets)"])
        self.assertNotIn("needs review:", " ".join(by_name["send_email"]["review"]))
        self.assertTrue(imported["needs_review"])
        self.assertGreater(imported["defaulted_unknown"], 0)
        self.assertIn("eff read(get_forecast), write(get_forecast)", imported["source"])

        openai = import_definitions(
            {
                "type": "function",
                "function": {
                    "name": "lookup_sku",
                    "description": "Read a stored sku for fictional Example Goods.",
                    "parameters": {"type": "object", "properties": {"sku": {"type": "string"}}, "required": ["sku"]},
                },
            },
            "auto",
        )
        self.assertEqual(openai["kind"], "openai")
        self.assertEqual(len(openai["ops"][0]["effects"]), 5)
        self.assertTrue(any(flag.startswith("needs review:") for flag in openai["ops"][0]["review"]))

    def test_content_length_input_is_accepted(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}).encode("utf-8")
        buffer = io.BytesIO(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        message = read_message(buffer)
        self.assertEqual(message["method"], "ping")
        out = io.BytesIO()
        write_message(out, {"jsonrpc": "2.0", "id": 7, "result": {}})
        wire = out.getvalue()
        self.assertTrue(wire.endswith(b"\n"))
        self.assertEqual(wire.count(b"\n"), 1)


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rpc = RpcProcess([sys.executable, str(ROOT / "etsah.py"), "mcp"])

    def tearDown(self) -> None:
        self.rpc.close()

    def test_initialize_list_and_calls(self) -> None:
        initialized = self.rpc.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "etsah-test", "version": "0"},
            },
        )
        result = initialized["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertEqual(result["serverInfo"]["name"], "etsah")
        self.assertIn("tools", result["capabilities"])
        self.rpc.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        ping = self.rpc.request("ping", {})
        self.assertEqual(ping["result"], {})

        listed = self.rpc.request("tools/list")
        tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
        self.assertEqual(
            set(tools),
            {"etsah_check", "etsah_import_tools", "etsah_explain", "etsah_check_plan"},
        )
        for tool in tools.values():
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIsInstance(tool["inputSchema"]["properties"], dict)
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertFalse(tool["annotations"]["destructiveHint"])
            self.assertFalse(tool["annotations"]["openWorldHint"])
        self.assertEqual(tools["etsah_check"]["inputSchema"]["required"], ["source"])
        self.assertEqual(tools["etsah_import_tools"]["inputSchema"]["required"], ["tools"])
        self.assertEqual(tools["etsah_explain"]["inputSchema"]["required"], ["code"])
        self.assertEqual(tools["etsah_check_plan"]["inputSchema"]["required"], ["source", "steps"])

        checked = _tool_payload(
            self.rpc.request(
                "tools/call",
                {"name": "etsah_check", "arguments": {"source": _read_contract("support_reply.etsah")}},
            )
        )
        self.assertTrue(checked["ok"])
        self.assertEqual(checked["plans"][0]["verdict"], ASK)
        self.assertEqual(checked["diagnostics"], [])

        explained = _tool_payload(
            self.rpc.request("tools/call", {"name": "etsah_explain", "arguments": {"code": "E-POLICY"}})
        )
        self.assertTrue(explained["known"])
        self.assertTrue(explained["suggested_fix"])

        imported = _tool_payload(
            self.rpc.request(
                "tools/call",
                {
                    "name": "etsah_import_tools",
                    "arguments": {
                        "kind": "mcp",
                        "tools": {
                            "tools": [
                                {
                                    "name": "get_forecast",
                                    "description": "Return a stored forecast for one sku.",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {"sku": {"type": "string"}},
                                        "required": ["sku"],
                                    },
                                }
                            ]
                        },
                    },
                },
            )
        )
        self.assertEqual(len(imported["ops"][0]["effects"]), 5)
        self.assertTrue(any(flag.startswith("needs review:") for flag in imported["ops"][0]["review"]))

        plan = _tool_payload(
            self.rpc.request(
                "tools/call",
                {
                    "name": "etsah_check_plan",
                    "arguments": {
                        "source": _read_contract("support_reply.etsah"),
                        "steps": [
                            {"tool": "tickets.read", "arguments": {"ticket_id": "T-1042"}},
                            {"tool": "mail.send_reply", "arguments": {}},
                            {"tool": "payments.charge"},
                        ],
                    },
                },
            )
        )
        self.assertFalse(plan["ok"])
        self.assertEqual([step["verdict"] for step in plan["steps"]], [ALLOW, ASK, BLOCK])
        self.assertTrue(plan["needs_approval"])
        self.assertEqual(plan["audit_id"], check_file(AGENTS / "support_reply.etsah").audit_id)

        missing = self.rpc.request("tools/call", {"name": "etsah_check", "arguments": {}})
        self.assertTrue(missing["result"]["isError"])
        unknown = self.rpc.request("no/such", {})
        self.assertEqual(unknown["error"]["code"], -32601)
        self.rpc.send({"jsonrpc": "2.0", "id": 99, "method": "tools/list", "params": "nope"})
        bad_params = self.rpc.read()
        self.assertEqual(bad_params["error"]["code"], -32602)

    def test_module_entry_point_initializes(self) -> None:
        other = RpcProcess([sys.executable, "-m", "etsah_mcp"])
        try:
            reply = other.request(
                "initialize",
                {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "mod", "version": "0"}},
            )
            self.assertEqual(reply["result"]["serverInfo"]["name"], "etsah")
            self.assertEqual(reply["result"]["protocolVersion"], "2024-11-05")
        finally:
            other.close()

    def test_a_bad_line_is_a_parse_error_and_the_server_continues(self) -> None:
        assert self.rpc.proc.stdin is not None
        self.rpc.proc.stdin.write(b"not-json\n")
        self.rpc.proc.stdin.flush()
        bad = self.rpc.read()
        self.assertEqual(bad["error"]["code"], -32700)
        ping = self.rpc.request("ping", {})
        self.assertEqual(ping["result"], {})


if __name__ == "__main__":
    unittest.main()
