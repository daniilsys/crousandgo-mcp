"""Offline tests: no network, no credentials, no real orders."""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools
from client import norm, parse_form, pickup_date
from errors import ShopError, classify_checkout, classify_izly

MENU = {"product_id": 1, "name": "Menu étudiant à 1€ du lundi 2 novembre", "service": "midi",
        "pickup_date": "2026-11-02", "price_cents": 100, "in_stock": True,
        "plats": ["Aiguillette de poulet pané, haricots verts", "Boulettes de blé Thaï, haricots verts"],
        "peripheriques": ["Fruit", "Compote", "Petit gâteau sec", "Rien"],
        "variations": [{"id": 11, "plat": "Boulettes de blé Thaï, haricots verts"},
                       {"id": 12, "plat": "Aiguillette de poulet pané, haricots verts"}], "url": "x"}


class FakeClient:
    def __init__(self, menus, orders=()):
        self._menus, self._orders = menus, list(orders)

    def menus(self):
        return self._menus

    def orders(self, detail=0):
        return self._orders


class ParsingTests(unittest.TestCase):
    def test_checkout_form_keeps_checked_and_selected_only(self):
        html = ('<form class="woocommerce-checkout checkout"><input name="a" value="1">'
                '<input type="checkbox" name="terms"><input type="radio" name="pm" value="izlyvl" checked>'
                '<select name="c"><option value="FR" selected>F</option></select></form>'
                '<input name="outside" value="x">')
        self.assertEqual(parse_form(html, "checkout"), {"a": "1", "pm": "izlyvl", "c": "FR"})

    def test_norm_ignores_accents_and_case(self):
        self.assertEqual(norm("  Petit GÂTEAU  sec"), "petit gateau sec")

    def test_pickup_date_from_dlc_then_name(self):
        p = {"name": "x", "attributes": [{"name": "DLC", "terms": [{"name": "02/11/2026"}]}]}
        self.assertEqual(pickup_date(p), "2026-11-02")
        self.assertIsNotNone(pickup_date({"name": "Menu du lundi 2 novembre", "attributes": []}))


class ErrorMappingTests(unittest.TestCase):
    def test_izly_insufficient_funds_has_a_user_message(self):
        e = classify_izly("Votre solde est insuffisant pour effectuer ce paiement. Veuillez recharger votre compte.")
        self.assertEqual(e.code, "insufficient_funds")
        self.assertIn("Top up", e.message)
        self.assertFalse(e.as_result()["ok"])

    def test_izly_other_cases(self):
        self.assertEqual(classify_izly("Identifiant ou mot de passe incorrect").code, "izly_bad_credentials")
        self.assertEqual(classify_izly("???").code, "izly_error")

    def test_checkout_cases(self):
        self.assertEqual(classify_checkout("Un seul repas à 1€ par commande").code, "already_ordered")
        self.assertEqual(classify_checkout("Produit en rupture de stock").code, "out_of_stock")
        self.assertEqual(classify_checkout("autre chose").code, "checkout_failed")


class OrderGuardTests(unittest.TestCase):
    def setUp(self):
        self._client = tools._client
        tools._client = FakeClient([MENU])
        tmp = Path(tempfile.mkdtemp())
        tools.AUDIT, tools.ASKED = tmp / "audit.jsonl", tmp / "asked.json"

    def tearDown(self):
        tools._client = self._client

    def test_dry_run_matches_choices_by_substring(self):
        r = tools.order_menu("2026-11-02", "poulet", "gateau", "fruit", reason="t")
        self.assertTrue(r["ok"] and r["dry_run"])
        self.assertTrue(r["would"]["plat"].startswith("Aiguillette"))
        self.assertEqual(r["would"]["peripheriques"], ["Petit gâteau sec", "Fruit"])

    def test_bad_choice_lists_options(self):
        r = tools.order_menu("2026-11-02", "pizza", "fruit", "fruit", reason="t")
        self.assertEqual(r["code"], "bad_choice")
        self.assertEqual(len(r["options"]), 2)

    def test_unpublished_menu_says_when_ordering_opens(self):
        r = tools.order_menu("2099-01-02", "poulet", "fruit", "fruit", reason="t")
        self.assertEqual(r["code"], "menu_not_open")

    def test_wrong_price_is_refused(self):
        tools._client = FakeClient([{**MENU, "price_cents": 450}])
        self.assertEqual(tools.order_menu("2026-11-02", "poulet", "fruit", "fruit", reason="t")["code"],
                         "unexpected_price")

    def test_real_order_needs_the_kill_switch(self):
        import os
        os.environ.pop("CROUSGO_ENABLE_WRITES", None)
        r = tools.order_menu("2026-11-02", "poulet", "fruit", "fruit", reason="t", dry_run=False)
        self.assertEqual(r["code"], "writes_disabled")


class GetOrdersTests(unittest.TestCase):
    def test_marks_only_paid_orders_to_collect_as_upcoming(self):
        old = tools._client
        paid = {"order_id": 1, "status": "En cours", "pickup_date": "2999-01-01"}
        tools._client = FakeClient([], [paid, {**paid, "status": "Annulée"},
                                        {**paid, "pickup_date": "2000-01-01", "status": "Terminée"}])
        try:
            flags = [o["upcoming"] for o in tools.get_orders()["orders"]]
        finally:
            tools._client = old
        self.assertEqual(flags, [True, False, False])


class EveningQuestionTests(unittest.TestCase):
    def setUp(self):
        self._client = tools._client
        tools.ASKED = Path(tempfile.mkdtemp()) / "asked.json"

    def tearDown(self):
        tools._client = self._client

    def test_asks_once_per_target_date(self):
        tools._client = FakeClient([MENU])
        friday = tools.evening_question(today=date(2026, 10, 30))
        self.assertIn("Monday 02/11", friday)
        self.assertIsNone(tools.evening_question(today=date(2026, 10, 31)))  # saturday
        self.assertIsNone(tools.evening_question(today=date(2026, 11, 1)))   # sunday

    def test_question_can_be_french(self):
        import os
        tools._client = FakeClient([MENU])
        os.environ["CROUSGO_LANG"] = "fr"
        try:
            self.assertIn("lundi 02/11", tools.evening_question(today=date(2026, 10, 30), mark=False))
        finally:
            del os.environ["CROUSGO_LANG"]

    def test_silent_when_already_ordered(self):
        tools._client = FakeClient([MENU], [{"order_id": 1, "status": "En cours", "pickup_date": "2026-11-02"}])
        self.assertIsNone(tools.evening_question(today=date(2026, 10, 30)))

    def test_cancelled_order_does_not_count(self):
        tools._client = FakeClient([MENU], [{"order_id": 1, "status": "Annulée", "pickup_date": "2026-11-02"}])
        self.assertIsNotNone(tools.evening_question(today=date(2026, 10, 30), mark=False))

    def test_silent_when_nothing_published_or_out_of_stock(self):
        tools._client = FakeClient([])
        self.assertIsNone(tools.evening_question(today=date(2026, 10, 30)))
        tools._client = FakeClient([{**MENU, "in_stock": False}])
        self.assertIsNone(tools.evening_question(today=date(2026, 10, 30)))

    def test_mark_false_does_not_record(self):
        tools._client = FakeClient([MENU])
        tools.evening_question(today=date(2026, 10, 30), mark=False)
        self.assertFalse(tools.ASKED.exists())


if __name__ == "__main__":
    unittest.main()
