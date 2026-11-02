"""Offline tests: no network, no credentials, no real orders."""
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from crousandgo import tools
from crousandgo.client import MONTHS, norm, parse_form, pickup_date
from crousandgo.errors import classify_checkout, classify_izly

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

    def test_pickup_date_falls_back_to_dlc_when_name_has_no_date(self):
        p = {"name": "x", "attributes": [{"name": "DLC", "terms": [{"name": "02/11/2026"}]}]}
        self.assertEqual(pickup_date(p), "2026-11-02")
        self.assertIsNotNone(pickup_date({"name": "Menu du lundi 2 novembre", "attributes": []}))

    def test_pickup_date_name_wins_over_dlc(self):
        # Real case: "Menu ... du mardi 3 novembre", made 02/11, DLC (use-by) 05/11.
        pickup = date.today() + timedelta(days=1)
        dlc = date.today() + timedelta(days=3)
        p = {"name": f"Menu étudiant à 1€ du jour {pickup.day} {MONTHS[pickup.month - 1]}",
             "attributes": [{"name": "Fabrication:", "terms": [{"name": date.today().strftime("%d/%m/%Y")}]},
                            {"name": "DLC", "terms": [{"name": dlc.strftime("%d/%m/%Y")}]}]}
        self.assertEqual(pickup_date(p), pickup.isoformat())


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
        self._lang = os.environ.get("CROUSGO_LANG")
        os.environ["CROUSGO_LANG"] = "en"  # a developer's .env must not change the expected text
        tools.ASKED = Path(tempfile.mkdtemp()) / "asked.json"

    def tearDown(self):
        tools._client = self._client
        if self._lang is None:
            os.environ.pop("CROUSGO_LANG", None)
        else:
            os.environ["CROUSGO_LANG"] = self._lang

    def test_asks_once_per_target_date(self):
        tools._client = FakeClient([MENU])
        friday = tools.evening_question(today=date(2026, 10, 30))
        self.assertIn("Monday 02/11", friday)
        self.assertIsNone(tools.evening_question(today=date(2026, 10, 31)))  # saturday
        self.assertIsNone(tools.evening_question(today=date(2026, 11, 1)))   # sunday

    def test_question_can_be_french(self):
        tools._client = FakeClient([MENU])
        os.environ["CROUSGO_LANG"] = "fr"
        self.assertIn("lundi 02/11", tools.evening_question(today=date(2026, 10, 30), mark=False))

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



class TopupTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        os.environ.update({"CROUSGO_ENABLE_WRITES": "1", "IZLY_TOPUP_EUR": "5", "IZLY_TOPUP_CARD": "2958"})
        tools._topups_done = 0

    def tearDown(self):
        os.environ.clear(); os.environ.update(self.env)

    def _call(self, **kw):
        from unittest import mock
        from crousandgo import izly
        eff = kw.pop("effect", {"topped_up_eur": 5, "balance_eur": 5.3})
        with mock.patch.object(tools, "AUDIT", Path(tempfile.mkdtemp()) / "a.jsonl"), \
             mock.patch.object(izly, "topup", side_effect=[eff] if isinstance(eff, dict) else eff) as top:
            return tools.topup_izly(reason="t", **kw), top

    def test_dry_run_is_default_and_does_nothing(self):
        res, top = self._call()
        self.assertTrue(res["dry_run"])
        top.assert_not_called()

    def test_real_topup_uses_config(self):
        res, top = self._call(dry_run=False)
        self.assertTrue(res["ok"])
        top.assert_called_once_with(5, "2958", timeout_s=300)

    def test_kill_switch(self):
        os.environ["CROUSGO_ENABLE_WRITES"] = "0"
        res, top = self._call(dry_run=False)
        self.assertEqual(res["code"], "writes_disabled")
        top.assert_not_called()

    def test_one_topup_per_process(self):
        self._call(dry_run=False)
        res, top = self._call(dry_run=False)
        self.assertEqual(res["code"], "budget_exhausted")
        top.assert_not_called()

    def test_bank_timeout_is_relayed(self):
        from crousandgo.errors import ShopError
        res, _ = self._call(dry_run=False, effect=[ShopError("topup_needs_verification", "no approval")])
        self.assertEqual(res["code"], "topup_needs_verification")
