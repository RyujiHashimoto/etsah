"""MCP and OpenAI tool lists become op declarations with marked effects."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
from prout import Checker, Parser
from prout_import import ToolImportError, import_mcp, import_openai, render_file

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "examples" / "agents" / "tools"


def _ops(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("op "):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    return blocks


def _effects(block: list[str]) -> list[str]:
    for line in block:
        if line.startswith("  eff "):
            return [part.strip() for part in line[6:].split(",")]
    return []


class MarkedEffectsTests(unittest.TestCase):
    def test_every_effect_is_named_in_a_comment(self) -> None:
        for path in (TOOLS / "mcp_tools.prout", TOOLS / "openai_tools.prout"):
            for block in _ops(path.read_text(encoding="utf-8")):
                comments = "\n".join(line for line in block if line.startswith("  # "))
                effects = _effects(block)
                self.assertTrue(comments, block[0])
                if effects:
                    for effect in effects:
                        self.assertIn(effect, comments, block[0])
                else:
                    self.assertIn("no effect was inferred", comments)

    def test_committed_output_matches_the_importer_and_the_cli(self) -> None:
        mcp_data = json.loads((TOOLS / "mcp_tools.json").read_text(encoding="utf-8"))
        openai_data = json.loads((TOOLS / "openai_tools.json").read_text(encoding="utf-8"))
        self.assertEqual(import_mcp(mcp_data), (TOOLS / "mcp_tools.prout").read_text(encoding="utf-8"))
        self.assertEqual(import_openai(openai_data), (TOOLS / "openai_tools.prout").read_text(encoding="utf-8"))
        for kind, filename in (("mcp", "mcp_tools.prout"), ("openai", "openai_tools.prout")):
            source = TOOLS / (filename.replace(".prout", ".json"))
            completed = subprocess.run(
                [sys.executable, str(ROOT / "prout.py"), "import", kind, str(source)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, (TOOLS / filename).read_text(encoding="utf-8"))
            self.assertEqual(completed.stderr, "")

    def test_sample_imports_parse_and_only_unlisted_hosts_fail(self) -> None:
        mcp = Checker(Parser((TOOLS / "mcp_tools.prout").read_text(encoding="utf-8")).parse()).check()
        openai = Checker(Parser((TOOLS / "openai_tools.prout").read_text(encoding="utf-8")).parse()).check()
        self.assertEqual([(item.code, item.message) for item in mcp], [
            ("E-POLICY", "network target 'send_email' is not allowed by policy"),
        ])
        self.assertEqual([(item.code, item.message) for item in openai], [
            ("E-POLICY", "network target 'send_customer_email' is not allowed by policy"),
        ])

    def test_annotation_map_and_name_review(self) -> None:
        text = import_mcp({
            "tools": [
                {
                    "name": "list_tickets",
                    "description": "List tickets",
                    "inputSchema": {"type": "object", "properties": {"queue": {"type": "string"}}, "required": ["queue"]},
                    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                },
                {
                    "name": "send-email",
                    "description": "Email the customer",
                    "inputSchema": {"type": "object", "properties": {}, "required": []},
                    "annotations": {"openWorldHint": True},
                },
                {
                    "name": "chargeCard",
                    "description": "Charge a card",
                    "inputSchema": {"type": "object", "properties": {"amount": {"type": "integer"}}, "required": ["amount"]},
                },
                {
                    "name": "payload_info",
                    "description": "Read a payload",
                    "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                },
                {
                    "name": "payment_link",
                    "description": "Build a payment link",
                    "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                },
            ]
        })
        blocks = {block[0]: block for block in _ops(text)}
        self.assertEqual(_effects(blocks["op list_tickets(queue: String) -> String"]), ["read(list_tickets)"])
        self.assertNotIn("destructiveHint", "\n".join(blocks["op list_tickets(queue: String) -> String"]))
        renamed = "\n".join(blocks["op send_email() -> String"])
        self.assertIn("was renamed to send_email", renamed)
        self.assertIn("inferred: openWorldHint -> net(send_email)", renamed)
        self.assertIn("review: name matches send -> emit(mail)", renamed)
        self.assertIn("review: name matches email -> emit(mail)", renamed)
        self.assertEqual(_effects(blocks["op send_email() -> String"]), ["net(send_email)", "emit(mail)"])
        self.assertEqual(_effects(blocks["op chargeCard(amount: Int) -> String"]), ["spend(payment)"])
        self.assertIn("review: name matches charge -> spend(payment)", "\n".join(blocks["op chargeCard(amount: Int) -> String"]))
        payload = "\n".join(blocks["op payload_info(id: String) -> String"])
        self.assertNotIn("spend", payload)
        self.assertIn("no effect was inferred", payload)
        self.assertEqual(_effects(blocks["op payment_link(id: String) -> String"]), ["spend(payment)"])

    def test_declared_effects_override_name_suggestions(self) -> None:
        text = import_mcp({
            "name": "post_status",
            "description": "Post a status",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            "effects": ["emit(board)"],
        })
        body = "\n".join(_ops(text)[0])
        self.assertIn("declared: effects were taken from the tool JSON: emit(board)", body)
        self.assertIn("not applied because effects were declared", body)
        self.assertEqual(_effects(_ops(text)[0]), ["emit(board)"])
        self.assertNotIn("eff emit(mail)", body)

    def test_false_hints_and_chargeback_do_not_invent_effects(self) -> None:
        text = import_openai([
            {
                "type": "function",
                "name": "chargeback_lookup",
                "description": "Look up a chargeback",
                "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
            }
        ])
        body = "\n".join(_ops(text)[0])
        self.assertFalse(any(line.startswith("  eff ") for line in body.splitlines()))
        self.assertIn("no effect was inferred", body)

    def test_both_hints_true_are_flagged_for_review(self) -> None:
        text = import_mcp({
            "name": "archive_thread",
            "description": "Archive a thread",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True, "destructiveHint": True, "idempotentHint": False},
        })
        body = "\n".join(_ops(text)[0])
        self.assertIn("readOnlyHint and destructiveHint are both true", body)
        self.assertIn("idempotentHint is not an effect", body)
        self.assertEqual(_effects(_ops(text)[0]), ["read(archive_thread)", "write(archive_thread)"])

    def test_output_schema_and_optional_properties(self) -> None:
        text = import_mcp({
            "name": "count_tickets",
            "description": "Count tickets",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "queue": {"type": "string"},
                    "note": {"type": ["string", "null"]},
                    "meta": {"type": "object"},
                },
                "required": ["queue"],
            },
            "outputSchema": {"type": "boolean"},
        })
        body = "\n".join(_ops(text)[0])
        self.assertTrue(body.startswith("op count_tickets(queue: String, note: String, meta: CountTicketsMeta) -> Bool"))
        self.assertIn("required omits note, meta", body)
        self.assertIn("parameter note allows null", body)
        self.assertIn("opaque type CountTicketsMeta", body)
        self.assertIn("type CountTicketsMeta", text)
        self.assertNotIn("placeholder", body)

    def test_flat_openai_and_wrapper_annotations(self) -> None:
        text = import_openai({
            "tools": [
                {
                    "type": "function",
                    "annotations": {"openWorldHint": True},
                    "function": {
                        "name": "lookup_order",
                        "description": "Look up an order",
                        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                    },
                }
            ]
        })
        body = "\n".join(_ops(text)[0])
        self.assertIn("inferred: openWorldHint -> net(lookup_order)", body)
        self.assertIn("Generated by `prout import openai`", text)

    def test_empty_list_is_marked(self) -> None:
        text = import_mcp({"tools": []})
        self.assertIn("the tool list was empty", text)
        self.assertNotIn("\nop ", text)

    def test_bad_input_is_an_error(self) -> None:
        with self.assertRaises(ToolImportError):
            import_mcp({"tools": [{"description": "missing name"}]})
        with self.assertRaises(ToolImportError):
            import_mcp({"name": "ping", "effects": ["fly(away)"]})
        with self.assertRaises(ToolImportError):
            import_mcp({"name": "ping", "effects": ["read(a)", "read(a)"]})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tools.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(ToolImportError):
                render_file("mcp", str(path))
        completed = subprocess.run(
            [sys.executable, str(ROOT / "prout.py"), "import", "mcp", str(TOOLS / "missing.json")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("[ERROR]", completed.stderr)
        self.assertEqual(completed.stdout, "")


if __name__ == "__main__":
    unittest.main()
