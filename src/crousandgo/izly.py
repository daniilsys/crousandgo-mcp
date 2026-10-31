"""Pay on rest.izly.fr with the configured Izly credentials.

Flow observed on the live site: the checkout redirects to
https://rest.izly.fr/api/Izly/WebPayments/<id>/Authorize?token=..., a form with #username and
#password and a "Valider" button. A refusal re-renders the same page with the reason in a
`.error` block (e.g. "Votre solde est insuffisant pour effectuer ce paiement.").
"""
import time
from urllib.parse import urlparse

from .config import secret
from .errors import ShopError, classify_izly

SHOP_HOST = "crousandgo.crous-strasbourg.fr"


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
