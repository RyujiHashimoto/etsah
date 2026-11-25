"""Playground labels, share links, and the static assets around them."""
from __future__ import annotations

import importlib.util
import json
import unittest
from html.parser import HTMLParser
from pathlib import Path

import support  # noqa: F401
from prout_api import check_file

ROOT = Path(__file__).resolve().parents[1]
PLAY = ROOT / "docs" / "playground"
SPEC = importlib.util.spec_from_file_location("playground_glue", PLAY / "glue.py")
glue = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(glue)

DEMO = importlib.util.spec_from_file_location("render_check_demo", ROOT / "docs" / "render_check_demo.py")
demo = importlib.util.module_from_spec(DEMO)
assert DEMO.loader is not None
DEMO.loader.exec_module(demo)

RUNTIME = ("prout.py", "prout_api.py", "prout_import.py")
SAMPLES = (
    "examples/agents/support_reply.prout",
    "examples/agents/support_reply_broken.prout",
    "examples/agents/invoice.prout",
    "examples/agents/subscription_cancel_broken.prout",
    "examples/agents/github_triage.prout",
    "examples/agents/tools/mcp_tools.json",
    "examples/agents/tools/openai_tools.json",
)


class TextPull(HTMLParser):
    def __init__(self, element_id: str) -> None:
        super().__init__()
        self.element_id = element_id
        self.capture = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if dict(attrs).get("id") == self.element_id:
            self.capture = True
            self.parts = []

    def handle_endtag(self, tag: str) -> None:
        if self.capture and tag in {"pre", "textarea"}:
            self.capture = False

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.parts.append(data)


def _verdict(payload: dict, kind: str, name: str) -> dict:
    return next(item for item in payload["verdicts"] if item["kind"] == kind and item["name"] == name)


class VerdictTests(unittest.TestCase):
    def test_missing_gate_is_block_and_the_send_is_ask(self) -> None:
        path = ROOT / "examples" / "agents" / "support_reply_broken.prout"
        payload = glue.check_payload(path.read_text(encoding="utf-8"))
        direct = check_file(path)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["ast_hash"], direct.ast_hash)
        self.assertEqual(payload["summary"]["verdict"], "BLOCK")
        self.assertIn("emit to an external party has no approval gate", payload["summary"]["reason"])
        task = _verdict(payload, "task", "draft_reply")
        self.assertEqual(task["verdict"], "BLOCK")
        self.assertEqual(task["line"], 35)
        send = _verdict(payload, "op", "mail.send_reply")
        self.assertEqual(send["verdict"], "ASK")
        self.assertIn("emit(mail)", send["reason"])
        read = _verdict(payload, "op", "tickets.read")
        self.assertEqual(read["verdict"], "ALLOW")
        self.assertEqual(payload["diagnostics"][0]["code"], "E-POLICY")
        self.assertIn("draft_reply", payload["plans"][0]["text"])

    def test_named_gate_asks_the_human_role(self) -> None:
        path = ROOT / "examples" / "agents" / "support_reply.prout"
        payload = glue.check_payload(path.read_text(encoding="utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["ast_hash"], check_file(path).ast_hash)
        self.assertEqual(payload["summary"]["verdict"], "ASK")
        task = _verdict(payload, "task", "draft_reply")
        self.assertEqual(task["verdict"], "ASK")
        self.assertIn("support_lead", task["reason"])
        self.assertIn("approve_reply", task["reason"])
        self.assertIn("emit(mail)", task["reason"])

    def test_invoice_spend_asks_and_cancel_without_a_gate_blocks(self) -> None:
        invoice = glue.check_payload((ROOT / "examples" / "agents" / "invoice.prout").read_text(encoding="utf-8"))
        self.assertEqual(invoice["summary"]["verdict"], "ASK")
        self.assertIn("billing_lead", _verdict(invoice, "task", "issue_invoice")["reason"])
        self.assertEqual(_verdict(invoice, "op", "payments.charge")["verdict"], "ASK")
        self.assertEqual(_verdict(invoice, "op", "ledger.draft")["verdict"], "ALLOW")
        cancel = glue.check_payload(
            (ROOT / "examples" / "agents" / "subscription_cancel_broken.prout").read_text(encoding="utf-8")
        )
        self.assertEqual(cancel["summary"]["verdict"], "BLOCK")
        self.assertIn("contract change", _verdict(cancel, "task", "cancel_subscription")["reason"])
        self.assertEqual(_verdict(cancel, "op", "subscriptions.cancel")["verdict"], "ASK")

    def test_triage_allowlist_and_a_blocked_host(self) -> None:
        path = ROOT / "examples" / "agents" / "github_triage.prout"
        text = path.read_text(encoding="utf-8")
        blocked = glue.check_payload(text)
        self.assertEqual(blocked["summary"]["verdict"], "BLOCK")
        self.assertIn("github", _verdict(blocked, "op", "github.list_issues")["reason"])
        allowed = glue.check_payload(text, "github")
        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["summary"]["verdict"], "ALLOW")
        self.assertEqual(_verdict(allowed, "task", "triage_open_issues")["verdict"], "ALLOW")
        broken = glue.check_payload(
            (ROOT / "examples" / "agents" / "github_triage_broken.prout").read_text(encoding="utf-8"),
            "github",
        )
        self.assertEqual(broken["summary"]["verdict"], "BLOCK")
        self.assertEqual(_verdict(broken, "op", "github.publish_gist")["verdict"], "BLOCK")
        self.assertIn("gist", _verdict(broken, "task", "triage_open_issues")["reason"])
        self.assertTrue(any("hourly rate limit" in item["message"] for item in broken["diagnostics"]))

    def test_a_warning_does_not_block(self) -> None:
        path = ROOT / "examples" / "refund_fixed.prout"
        payload = glue.check_payload(path.read_text(encoding="utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["verdict"], "ALLOW")
        self.assertTrue(any(item["severity"] == "warning" for item in payload["diagnostics"]))

    def test_parse_error_has_no_hash(self) -> None:
        payload = glue.check_payload("op nope\n")
        self.assertEqual(payload["summary"]["verdict"], "BLOCK")
        self.assertEqual(payload["ast_hash"], "")
        self.assertEqual(payload["verdicts"], [])
        self.assertEqual(payload["diagnostics"][0]["code"], "E-PARSE")

    def test_import_round_trip_and_bad_json(self) -> None:
        raw = (ROOT / "examples" / "agents" / "tools" / "mcp_tools.json").read_text(encoding="utf-8")
        imported = json.loads(glue.import_text("mcp", raw))
        self.assertTrue(imported["ok"])
        self.assertIn("op send_email", imported["source"])
        self.assertIn("eff net(send_email), emit(mail)", imported["source"])
        bad = json.loads(glue.import_text("openai", "{"))
        self.assertFalse(bad["ok"])
        self.assertIn("invalid JSON", bad["error"])
        unknown = json.loads(glue.import_text("other", "[]"))
        self.assertIn("kind", unknown["error"])


class ShareTests(unittest.TestCase):
    def test_share_token_round_trips_unicode_source(self) -> None:
        source = "op 読む() -> Bool\n  eff read(tickets)\n  impl native\n"
        token = glue.encode_share(source, "github", "check", "")
        self.assertTrue(token.startswith("z."))
        self.assertNotIn(source, token)
        self.assertEqual(
            glue.decode_share("#" + token),
            {"s": source, "n": "github", "m": "check", "k": ""},
        )

    def test_bad_share_token_raises(self) -> None:
        with self.assertRaises(ValueError):
            glue.decode_share("z.not-valid")


class AssetTests(unittest.TestCase):
    def test_runtime_copies_match_the_repository_sources(self) -> None:
        for name in RUNTIME:
            copy = PLAY / "runtime" / name
            self.assertEqual(copy.read_bytes(), (ROOT / name).read_bytes(), name)

    def test_sample_copies_match_the_examples(self) -> None:
        for rel in SAMPLES:
            copy = PLAY / "samples" / rel.removeprefix("examples/")
            self.assertEqual(copy.read_bytes(), (ROOT / rel).read_bytes(), rel)

    def test_page_is_english_static_and_points_at_the_checker(self) -> None:
        html = (PLAY / "index.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="en">', html)
        self.assertIn('meta name="description"', html)
        self.assertIn('property="og:image"', html)
        self.assertIn('name="twitter:card"', html)
        self.assertIn("summary_large_image", html)
        self.assertIn("og.png", html)
        self.assertIn(">Run<", html)
        self.assertIn("Reply, blocked", html)
        self.assertIn("Reply, gated", html)
        self.assertIn("Invoice", html)
        self.assertIn("Cancel plan", html)
        self.assertIn("Issue triage", html)
        self.assertIn("Import tools", html)
        self.assertIn("ALLOW", html)
        self.assertIn("BLOCK", html)
        self.assertIn("ASK", html)
        self.assertIn("deflate-raw", html)
        self.assertIn('"runtime/" + name', html)
        self.assertIn('"../../" + name', html)
        self.assertIn("glue.py", html)
        parser = TextPull("static-example")
        parser.feed(html)
        shown = "".join(parser.parts).strip() + "\n"
        original = (ROOT / "examples" / "agents" / "support_reply_broken.prout").read_text(encoding="utf-8").strip() + "\n"
        self.assertEqual(shown, original)
        self.assertIn("emit to an external party has no approval gate", html)

    def test_demo_svg_is_the_real_command_output(self) -> None:
        svg = (ROOT / "docs" / "check-demo.svg").read_text(encoding="utf-8")
        self.assertEqual(svg, demo.render_svg())
        self.assertIn("E-POLICY", svg)
        self.assertIn("emit to an external party has no approval gate", svg)
        self.assertIn("OK — no issues", svg)
        self.assertIn("gate: approve_reply", svg)
        self.assertNotIn("<script", svg)

    def test_comparison_answers_the_wrapper_question_without_a_first_claim(self) -> None:
        text = (ROOT / "docs" / "en" / "comparison.md").read_text(encoding="utf-8")
        self.assertIn("Why not a Cedar or OPA policy plus a wrapper?", text)
        self.assertIn("sequence", text)
        for url in (
            "https://www.eiffel.org/doc/eiffel/ET-_Design_by_Contract_%28tm%29%2C_Assertions_and_Exceptions",
            "https://github.com/dafny-lang/dafny/blob/master/docs/DafnyRef/Specifications.md",
            "https://koka-lang.github.io/koka/doc/book.html",
            "https://www.openpolicyagent.org/docs/latest/",
            "https://docs.cedarpolicy.com/",
            "https://docs.nvidia.com/nemo/guardrails/about-nemo-guardrails-library/rail-types",
            "https://github.com/guardrails-ai/guardrails/blob/main/README.md",
            "https://modelcontextprotocol.org/specification/2026-07-28/server/tools",
        ):
            self.assertIn(url, text, url)
        lowered = text.lower()
        self.assertNotIn("world first", lowered)
        self.assertNotIn("world's first", lowered)
        self.assertNotIn("only tool", lowered)

    def test_og_image_is_1200_by_630(self) -> None:
        data = (PLAY / "og.png").read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(int.from_bytes(data[16:20], "big"), 1200)
        self.assertEqual(int.from_bytes(data[20:24], "big"), 630)


if __name__ == "__main__":
    unittest.main()
