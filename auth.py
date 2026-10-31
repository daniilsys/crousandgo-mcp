"""Log in to Crous & Go with Chromium and keep a single portable state file.

    cli.py login           # headless, uses CROUS_USER / CROUS_PASS
    cli.py login --manual  # visible browser: log in by hand, writes session.json

The state file (WordPress cookies) is rewritten after every successful login. Treat it like
a password.
"""
from config import BASE, SESSION, secret, write_private
from errors import ShopError
import json

LOGIN_URL = BASE + "/mon-compte/"
NAV = ".woocommerce-MyAccount-navigation"


def _logged_in(page):
    return page.locator(NAV).count() > 0


def login(manual=False):
    from playwright.sync_api import sync_playwright

    user, pwd = secret("CROUS_USER"), secret("CROUS_PASS")
    if not manual and not (user and pwd):
        raise ShopError("missing_credentials",
                        "CROUS_USER / CROUS_PASS are not set. Put them in "
                        "~/.config/crousandgo/.env (see .env.example).")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not manual)
        kw = {"storage_state": json.loads(SESSION.read_text())} if SESSION.exists() else {}
        ctx = browser.new_context(**kw)
        page = ctx.new_page()
        page.set_default_timeout(60_000)  # the site is slow
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        if not _logged_in(page):
            if manual:
                print("Log in in the browser window. Waiting for the login to finish...")
                page.wait_for_selector(NAV, timeout=600_000)
            else:
                page.fill("#username", user)
                page.fill("#password", pwd)
                # a third-party cookie iframe overlays the button: submit with Enter instead
                page.press("#password", "Enter")
                page.wait_for_load_state("domcontentloaded")
                if not _logged_in(page):
                    err = page.locator(".woocommerce-error").first
                    msg = err.inner_text().strip() if err.count() else ""
                    browser.close()
                    raise ShopError("crous_bad_credentials",
                                    "Crous & Go refused the login. Check CROUS_USER / CROUS_PASS.",
                                    site_message=msg)
        write_private(SESSION, json.dumps(ctx.storage_state()))
        browser.close()
