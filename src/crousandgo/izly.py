"""Pay on rest.izly.fr with the configured Izly credentials.

Flow observed on the live site: the checkout redirects to
https://rest.izly.fr/api/Izly/WebPayments/<id>/Authorize?token=..., a form with #username and
#password and a "Valider" button. A refusal re-renders the same page with the reason in a
`.error` block (e.g. "Votre solde est insuffisant pour effectuer ce paiement.").
"""
import re
import time
from urllib.parse import urlparse

from .config import secret
from .client import norm
from .errors import ShopError, classify_izly

SHOP_HOST = "crousandgo.crous-strasbourg.fr"
ESPACE = "https://mon-espace.izly.fr"
ESPACE_HOST = "mon-espace.izly.fr"
TOPUP_AMOUNTS = (5, 10, 15, 20)  # the preset buttons of the card top-up page


def _keypad(page, code):
    """Type the secret code on Izly's on-screen keypad (the input itself is read-only)."""
    for digit in code:
        page.locator("button:visible", has_text=re.compile(f"^{digit}$")).first.click()


def _balance(page):
    """Balance in cents read from the home page, or None when it cannot be read."""
    page.goto(ESPACE + "/", wait_until="domcontentloaded")
    m = re.search(r"Solde au.*?([+-]?\d+),(\d{2})\s*€", page.inner_text("body"), re.S)
    if not m:
        return None
    euros = int(m.group(1))
    return euros * 100 + (-1 if m.group(1).startswith("-") else 1) * int(m.group(2))


def topup(amount_eur, card="", timeout_s=300):
    """Top up the Izly account with a card already saved on it (mon-espace.izly.fr).

    `card` selects the saved card by its label in Izly's list (e.g. "2958" or "497801XXXXXX2958"),
    case and accents ignored; empty means the first saved card.

    Flow: log in (keypad), Recharger > carte bancaire, amount, saved card, Valider, Confirmer,
    secret code, \"Effectuer le rechargement\". Success is decided by the balance going up, not by
    the wording of a result page. If the bank asks for its own approval (e.g. a push in its app), the
    user gives it meanwhile; without it after `timeout_s` this fails with `topup_needs_verification`."""
    from playwright.sync_api import sync_playwright

    if amount_eur not in TOPUP_AMOUNTS:
        raise ShopError("bad_topup_amount", f"Top-up amount must be one of {TOPUP_AMOUNTS} EUR.")
    user, pwd = secret("IZLY_USER"), secret("IZLY_PASS")
    if not (user and pwd):
        raise ShopError("missing_izly_credentials",
                        "IZLY_USER / IZLY_PASS are not set. Put them in "
                        "~/.config/crousandgo/.env (see .env.example).")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(60_000)
        try:
            page.goto(ESPACE + "/", wait_until="domcontentloaded")
            for label in ("Decline All", "Refuser"):
                try:
                    page.get_by_role("button", name=label).click(timeout=2000)
                    break
                except Exception:
                    pass
            page.fill("#Username", user)
            _keypad(page, pwd)
            page.click("#login")
            page.wait_for_load_state("domcontentloaded")
            if page.locator("#Password").count():
                raise ShopError("izly_bad_credentials",
                                "Izly refused the configured login (IZLY_USER / IZLY_PASS). Check them.")
            before = _balance(page)

            page.goto(ESPACE + "/Home/Recharge", wait_until="domcontentloaded")
            page.locator("button, a, div, span, label", has_text=re.compile(rf"^{amount_eur} €$")).first.click()
            labels = [t.strip() for t in page.locator("#cbList option").all_inner_texts()]
            saved = [i for i, t in enumerate(labels) if re.search(r"\d{6}X+\d{4}", t)]
            wanted = next((i for i in saved if norm(card) in norm(labels[i])), None) if card else (saved or [None])[0]
            if wanted is None:
                raise ShopError("topup_card_not_found",
                                (f'No saved card matching "{card}" on the Izly account. ' if card
                                 else "No saved bank card on the Izly account. ")
                                + "Saved cards: " + (" | ".join(labels[i] for i in saved) or "none") + ".",
                                cards=[labels[i] for i in saved])
            page.select_option("#cbList", index=wanted)
            page.locator("input#validate:visible").first.click()      # Valider
            page.locator("input#validate:visible").first.click()      # Confirmer
            _keypad(page, pwd)
            page.locator("input#validate:visible").first.click()      # Effectuer le rechargement

            # The bank may ask for its own approval (push in its app, SMS code page...). The user gives it
            # on their side; meanwhile the balance is polled on a second page so the first one is left alone.
            watcher = page.context.new_page()
            at_bank = False
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                page.wait_for_timeout(3000)
                host = urlparse(page.url).hostname
                at_bank = at_bank or bool(host and host != ESPACE_HOST)
                if host == ESPACE_HOST:
                    err = page.locator(".error:visible, .alert-danger:visible")
                    if err.count() and err.first.inner_text().strip():
                        raise ShopError("topup_refused",
                                        f"Izly refused the top-up: {err.first.inner_text().strip()}")
                after = _balance(watcher)
                if before is not None and after is not None and after >= before + amount_eur * 100:
                    return {"topped_up_eur": amount_eur, "balance_eur": after / 100}
            if at_bank:
                raise ShopError("topup_needs_verification",
                                "The bank was waiting for the approval of the top-up and did not get it in "
                                f"time ({timeout_s} s). Nothing was added to the Izly balance.")
            raise ShopError("topup_not_confirmed",
                            "The top-up was submitted but the balance did not go up. Check the Izly "
                            "account before trying again.")
        finally:
            browser.close()


def pay(url, timeout_s=90):
    from playwright.sync_api import sync_playwright

    user, pwd = secret("IZLY_USER"), secret("IZLY_PASS")
    if not (user and pwd):
        raise ShopError("missing_izly_credentials",
                        "IZLY_USER / IZLY_PASS are not set. Put them in "
                        "~/.config/crousandgo/.env (see .env.example).")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(60_000)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.fill("#username", user)
            page.fill("#password", pwd)
            page.get_by_role("button", name="Valider").click()
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                if urlparse(page.url).hostname == SHOP_HOST:
                    return {"final_url": page.url}
                if page.url.split("?")[0].endswith("/Summary"):
                    # Izly's result page (also shown after a cancel): the order status decides
                    return {"summary": page.inner_text("main")[:300]}
                err = page.locator(".error")
                if err.count() and err.first.inner_text().strip():
                    raise classify_izly(err.first.inner_text().strip())
                page.wait_for_timeout(500)
            return {"timeout": True}  # unknown outcome: the caller reads the order status
        finally:
            browser.close()


def order_pay_fields(html):
    """Fields of the pay-for-order form (#order_review) with Izly selected.

    Checked boxes and radios only, as a browser would send them. Nothing is paid here: the caller
    posts these fields to the order-pay URL and gets the Izly authorisation URL back."""
    m = re.search(r'<form id="order_review".*?</form>', html, re.S)
    if not m:
        raise ShopError("checkout_failed", "Pay-for-order form not found on the order page.")
    fields = {}
    for tag in re.findall(r"<input[^>]*>", m.group(0)):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        if re.search(r'type="(checkbox|radio)"', tag) and "checked" not in tag:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        fields[name.group(1)] = value.group(1) if value else ""
    fields.update({"payment_method": "izlyvl", "terms": "on", "terms-field": "1",
                   "place_order": "Payer la commande"})
    return fields


def pay_pending_order(http, pay_url):
    """Submit the pay-for-order form of an existing order and return the Izly authorisation URL.

    This only starts the Izly step: the debit happens on Izly's own page, in pay()."""
    page = http.get(pay_url)
    r = http.post(pay_url, data=order_pay_fields(page.text), follow_redirects=False)
    location = r.headers.get("location", "")
    if r.status_code not in (301, 302, 303) or "izly" not in location:
        raise ShopError("checkout_failed", "Crous & Go did not redirect to Izly.", status=r.status_code)
    return location
