"""Information-flow labels: syntax, inference, sinks, declassify, and hashes."""
from __future__ import annotations

import json
import unittest

from support import ROOT, check, codes

NET = ["email", "analytics", "health"]


def run(text: str):
    return check(text, allow_net=NET)


class TestLabelSyntax(unittest.TestCase):
    def test_unknown_label(self) -> None:
        diags = run(
            """
            label pii
            flow f(x: String @nope) -> String
              return x
            """
        )
        self.assertIn("E-LABEL", codes(diags, "error"))

    def test_public_label_rejected(self) -> None:
        diags = run(
            """
            label public
            """
        )
        self.assertIn("E-LABEL", codes(diags, "error"))

    def test_public_annotation_rejected(self) -> None:
        diags = run(
            """
            label pii
            flow f(x: String @public) -> String
              return x
            """
        )
        self.assertIn("E-LABEL", codes(diags, "error"))

    def test_duplicate_label_on_type(self) -> None:
        diags = run(
            """
            label pii
            flow f(x: String @pii @pii) -> String
              return x
            """
        )
        self.assertIn("E-LABEL", codes(diags, "error"))

    def test_record_annotation_rejected(self) -> None:
        diags = run(
            """
            label internal
            type Id
            type Customer @internal {
              id: Id
            }
            """
        )
        self.assertIn("E-LABEL", codes(diags, "error"))

    def test_label_in_type_argument_rejected(self) -> None:
        diags = run(
            """
            label pii
            type Box
            flow f(x: Box<String @pii>) -> String
              return "x"
            """
        )
        self.assertIn("E-LABEL", codes(diags, "error"))


class TestArgumentsAndReturns(unittest.TestCase):
    def test_public_to_pii_ok(self) -> None:
        diags = run(
            """
            label pii
            op take(x: String @pii) -> Bool
              impl native
            flow f() -> Bool
              return take("hello")
            """
        )
        self.assertEqual(diags, [])

    def test_pii_to_public_argument(self) -> None:
        diags = run(
            """
            label pii
            type Customer {
              email: String @pii
            }
            op take(x: String) -> Bool
              impl native
            flow f(c: Customer) -> Bool
              return take(c.email)
            """
        )
        self.assertIn("E-IFLOW-ARG", codes(diags, "error"))

    def test_secret_to_pii_argument(self) -> None:
        diags = run(
            """
            label pii
            label secret
            type Customer {
              api_key: String @secret
            }
            op take(x: String @pii) -> Bool
              impl native
            flow f(c: Customer) -> Bool
              return take(c.api_key)
            """
        )
        self.assertIn("E-IFLOW-ARG", codes(diags, "error"))

    def test_pii_return_to_public(self) -> None:
        diags = run(
            """
            label pii
            type Customer {
              email: String @pii
            }
            flow f(c: Customer) -> String
              return c.email
            """
        )
        self.assertIn("E-IFLOW-RET", codes(diags, "error"))

    def test_public_return_to_pii_ok(self) -> None:
        diags = run(
            """
            label pii
            flow f() -> String @pii
              return "hello"
            """
        )
        self.assertEqual(diags, [])

    def test_pre_uses_pii(self) -> None:
        diags = run(
            """
            label pii
            type Customer {
              email: String @pii
            }
            op find(c: Customer) -> Bool
              pre c.email != ""
              impl native
            """
        )
        self.assertIn("E-IFLOW-CONTRACT", codes(diags, "error"))

    def test_post_uses_pii(self) -> None:
        diags = run(
            """
            label pii
            type Customer {
              email: String @pii
            }
            op find(c: Customer) -> Customer
              post result.email != ""
              impl native
            """
        )
        self.assertIn("E-IFLOW-CONTRACT", codes(diags, "error"))


class TestSinks(unittest.TestCase):
    def test_sink_accepts_pii(self) -> None:
        diags = run(
            """
            label pii
            type Customer {
              email: String @pii
            }
            policy
              sink net(email) accepts pii
            op send(to: String @pii) -> Bool
              eff net(email)
              sends to -> net(email)
              impl native
            flow f(c: Customer) -> Bool
              eff net(email)
              return send(c.email)
            """
        )
        self.assertEqual(diags, [])

    def test_sink_rejects_secret(self) -> None:
        diags = run(
            """
            label secret
            policy
              sink net(email) rejects secret
            op send(to: String @secret) -> Bool
              eff net(email)
              sends to -> net(email)
              impl native
            """
        )
        self.assertIn("E-IFLOW-SINK", codes(diags, "error"))

    def test_wildcard_reject_absolute(self) -> None:
        diags = run(
            """
            label pii
            label secret
            policy
              sink net(*) accepts pii, secret
              sink net(*) rejects secret
            op send(to: String @secret) -> Bool
              eff net(email)
              sends to -> net(email)
              impl native
            """
        )
        self.assertIn("E-IFLOW-SINK", codes(diags, "error"))

    def test_sink_rule_cannot_accept_public(self) -> None:
        diags = run(
            """
            label pii
            policy
              sink net(email) accepts public
            """
        )
        self.assertIn("E-LABEL", codes(diags, "error"))

    def test_sink_read_is_not_a_sink(self) -> None:
        diags = run(
            """
            label pii
            policy
              sink read(customers) accepts pii
            """
        )
        self.assertIn("E-POLICY", codes(diags, "error"))


class TestSends(unittest.TestCase):
    def test_sends_unknown_effect(self) -> None:
        diags = run(
            """
            label pii
            op send(to: String @pii) -> Bool
              eff net(email)
              sends to -> net(analytics)
              impl native
            """
        )
        self.assertIn("E-SINK", codes(diags, "error"))

    def test_sends_unknown_parameter(self) -> None:
        diags = run(
            """
            label pii
            op send(to: String @pii) -> Bool
              eff net(email)
              sends nope -> net(email)
              impl native
            """
        )
        self.assertIn("E-SINK", codes(diags, "error"))

    def test_native_requires_sends(self) -> None:
        diags = run(
            """
            label pii
            op save(c: String @pii) -> Bool
              eff write(db)
              impl native
            """
        )
        self.assertIn("E-SINK-UNBOUND", codes(diags, "error"))

    def test_native_sends_none_ok(self) -> None:
        diags = run(
            """
            label pii
            op health() -> Bool
              eff net(health)
              sends none -> net(health)
              impl native
            """
        )
        self.assertEqual(diags, [])

    def test_native_read_then_sink_requires_sends(self) -> None:
        diags = run(
            """
            label pii
            op aggregate() -> Bool
              eff read(events), net(analytics)
              impl native
            """
        )
        self.assertIn("E-SINK-UNBOUND", codes(diags, "error"))

    def test_native_read_then_sink_sends_none_ok(self) -> None:
        diags = run(
            """
            label pii
            op aggregate() -> Bool
              eff read(events), net(analytics)
              sends none -> net(analytics)
              impl native
            """
        )
        self.assertEqual(diags, [])

    def test_sends_on_expression_impl_rejected(self) -> None:
        diags = run(
            """
            label pii
            op f(x: String @pii) -> String
              sends x -> net(email)
              impl x
            """
        )
        self.assertIn("E-SINK", codes(diags, "error"))


class TestDeclassify(unittest.TestCase):
    PRELUDE = """
        label pii
        type Id
        type Customer {
          id: Id
          email: String @pii
        }
        op customers.get(id: Id) -> Customer
          eff read(customers)
          impl native
        op track_event(event: String) -> Bool
          eff net(analytics)
          sends event -> net(analytics)
          impl native
    """

    def test_declassify_allowed(self) -> None:
        diags = run(
            self.PRELUDE
            + """
        policy
          declassify pii in masked require role support
        flow masked(id: Id) -> Bool
          eff read(customers), net(analytics)
          let c = customers.get(id)
          let m = declassify(c.email, pii, "分析用にマスクする")
          return track_event(m)
        """
        )
        self.assertEqual(codes(diags, "error"), [])
        self.assertIn("W-DECLASS", codes(diags, "warning"))

    def test_declassify_not_allowed(self) -> None:
        diags = run(
            self.PRELUDE
            + """
        flow masked(id: Id) -> Bool
          eff read(customers), net(analytics)
          let c = customers.get(id)
          let m = declassify(c.email, pii, "分析用にマスクする")
          return track_event(m)
        """
        )
        self.assertIn("E-DECLASS", codes(diags, "error"))

    def test_declassify_empty_reason(self) -> None:
        diags = run(
            self.PRELUDE
            + """
        policy
          declassify pii in masked require role support
        flow masked(id: Id) -> String
          eff read(customers)
          let c = customers.get(id)
          let m = declassify(c.email, pii, "   ")
          return m
        """
        )
        self.assertIn("E-DECLASS", codes(diags, "error"))

    def test_declassify_newline_reason(self) -> None:
        diags = run(
            self.PRELUDE
            + """
        policy
          declassify pii in masked require role support
        flow masked(id: Id) -> String
          eff read(customers)
          let c = customers.get(id)
          let m = declassify(c.email, pii, "line1\\nline2")
          return m
        """
        )
        self.assertIn("E-DECLASS", codes(diags, "error"))

    def test_declassify_absent_label(self) -> None:
        diags = run(
            """
            label pii
            label secret
            type Customer {
              email: String @pii
            }
            policy
              declassify secret in f require role support
            flow f(c: Customer) -> String
              let x = declassify(c.email, secret, "x")
              return x
            """
        )
        self.assertIn("E-DECLASS", codes(diags, "error"))

    def test_declassify_record_rejected(self) -> None:
        diags = run(
            """
            label pii
            type Customer {
              email: String @pii
            }
            policy
              declassify pii in f require role support
            flow f(c: Customer) -> Bool
              let x = declassify(c, pii, "x")
              return true
            """
        )
        self.assertIn("E-DECLASS", codes(diags, "error"))

    def test_declassify_in_contract_rejected(self) -> None:
        diags = run(
            """
            label pii
            type Customer {
              email: String @pii
            }
            policy
              declassify pii in find require role support
            op find(c: Customer) -> Bool
              pre declassify(c.email, pii, "x") != ""
              impl native
            """
        )
        self.assertIn("E-DECLASS", codes(diags, "error"))


class TestInference(unittest.TestCase):
    def test_binop_label_union(self) -> None:
        diags = run(
            """
            label pii
            type Customer {
              email: String @pii
            }
            flow f(c: Customer) -> String
              return c.email + "!"
            """
        )
        self.assertIn("E-IFLOW-RET", codes(diags, "error"))

    def test_comparison_labeled(self) -> None:
        diags = run(
            """
            label pii
            type Customer {
              email: String @pii
            }
            flow f(c: Customer) -> Bool
              return c.email == "a@b"
            """
        )
        self.assertIn("E-IFLOW-RET", codes(diags, "error"))

    def test_field_access_narrow_label(self) -> None:
        diags = run(
            """
            label pii
            label secret
            type Id
            type Customer {
              id: Id
              email: String @pii
              api_key: String @secret
            }
            flow f(c: Customer) -> Id
              return c.id
            """
        )
        self.assertEqual(diags, [])

    def test_record_union_label(self) -> None:
        diags = run(
            """
            label pii
            label secret
            type Id
            type Customer {
              id: Id
              email: String @pii
              api_key: String @secret
            }
            policy
              sink write(customers) accepts pii
            op save(c: Customer) -> Bool
              eff write(customers)
              sends c -> write(customers)
              impl native
            """
        )
        self.assertIn("E-IFLOW-SINK", codes(diags, "error"))


class TestHashes(unittest.TestCase):
    def test_hash_unchanged_without_labels(self) -> None:
        from support import parse
        from prout import ast_hash

        text = (ROOT / "examples" / "refund_fixed.prout").read_text(encoding="utf-8")
        self.assertEqual(ast_hash(parse(text)), "bae8501e8b3f")

    def test_label_order_irrelevant(self) -> None:
        from support import parse
        from prout import ast_hash

        a = parse(
            """
            label pii
            label secret
            op f(x: String @pii @secret) -> String
              impl native
            """
        )
        b = parse(
            """
            label pii
            label secret
            op f(x: String @secret @pii) -> String
              impl native
            """
        )
        self.assertEqual(ast_hash(a), ast_hash(b))

    def test_policy_order_irrelevant(self) -> None:
        from support import parse
        from prout import ast_hash

        a = parse(
            """
            label pii
            label secret
            policy
              sink net(email) accepts pii
              sink net(email) rejects secret
            """
        )
        b = parse(
            """
            label pii
            label secret
            policy
              sink net(email) rejects secret
              sink net(email) accepts pii
            """
        )
        self.assertEqual(ast_hash(a), ast_hash(b))

    def test_no_labels_skips_information_flow(self) -> None:
        diags = run(
            """
            type Customer {
              email: String
            }
            flow f(c: Customer) -> String
              return c.email
            """
        )
        self.assertEqual(diags, [])


class TestSerialization(unittest.TestCase):
    def test_ast_contains_labels_and_policy(self) -> None:
        from support import parse
        from prout import program_to_dict

        program = parse(
            """
            label pii "個人情報"
            type Customer {
              email: String @pii
            }
            policy
              sink net(email) accepts pii
            op send(to: String @pii) -> Bool
              eff net(email)
              sends to -> net(email)
              impl native
            """
        )
        data = program_to_dict(program)
        self.assertEqual(data["labels"], [{"name": "pii", "doc": "個人情報"}])
        self.assertEqual(data["types"][0]["field_labels"], {"email": ["pii"]})
        self.assertEqual(data["ops"][0]["param_labels"], {"to": ["pii"]})
        self.assertEqual(data["ops"][0]["sends"], [{"args": ["to"], "effect": ["net", "email"]}])
        self.assertEqual(data["policy"]["sinks"][0]["labels"], ["pii"])

    def test_unlabeled_ast_omits_new_keys(self) -> None:
        from support import parse
        from prout import program_to_dict

        data = program_to_dict(
            parse(
                """
                type Customer {
                  email: String
                }
                flow f(c: Customer) -> String
                  return c.email
                """
            )
        )
        self.assertNotIn("labels", data)
        self.assertNotIn("policy", data)
        self.assertNotIn("field_labels", data["types"][0])
        self.assertNotIn("param_labels", data["flows"][0])

    def test_json_context_for_argument(self) -> None:
        diags = run(
            """
            label pii
            type Customer {
              email: String @pii
            }
            op take(x: String) -> Bool
              impl native
            flow f(c: Customer) -> Bool
              return take(c.email)
            """
        )
        arg = next(d for d in diags if d.code == "E-IFLOW-ARG")
        payload = json.loads(json.dumps(arg.as_dict()))
        self.assertEqual(payload["context"]["argument"], "x")
        self.assertEqual(payload["context"]["expected_labels"], [])
        self.assertEqual(payload["context"]["actual_labels"], ["pii"])


if __name__ == "__main__":
    unittest.main()
