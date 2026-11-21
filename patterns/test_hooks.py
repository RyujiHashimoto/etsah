from pathlib import Path
import unittest

from proutc import load_registry, parse_app, validate


class HookValidationTests(unittest.TestCase):
    def test_checkout_success_targets(self):
        root = Path(__file__).parent
        registry = load_registry(root / "registry")
        shop = (root / "examples/shop.prout").read_text(encoding="utf-8")
        for target, valid in [
            ("fulfill", True),   # Custom block declared later.
            ("auth", True),      # Applied pattern.
            ("notify", False),   # Registered but not applied.
            ("fulflll", False),  # Unknown target.
            (None, True),        # Optional hook omitted.
        ]:
            with self.subTest(target=target):
                app = parse_app(shop)
                checkout = next(p for p in app.patterns if p.name == "checkout")
                if target is None:
                    del checkout.slots["success"]
                else:
                    checkout.slots["success"] = target
                errors = [d for d in validate(app, registry) if d.code == "E-HOOK"]
                self.assertEqual(len(errors), 0 if valid else 1)
                if errors:
                    self.assertIn("checkout.success", errors[0].message)
                    self.assertIn(target, errors[0].message)
