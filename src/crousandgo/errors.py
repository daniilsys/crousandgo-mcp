"""Failures the agent can relay to the user in their language.

Each carries a stable `code` and an English `message` written for the end user, so the agent
only has to pass it on.
"""


class ShopError(Exception):
    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def as_result(self):
        return {"ok": False, "code": self.code, "message": self.message, **self.extra}


def classify_izly(text):
    """Map the text of Izly's `.error` block to a ShopError."""
    t = (text or "").lower()
    if "solde" in t and "insuffisant" in t:
        return ShopError(
            "insufficient_funds",
            "Izly balance too low to pay the 1 EUR menu. Top up the Izly account, then retry the "
            "order (the site cancels the failed order automatically).",
            izly_message=text)
    if any(w in t for w in ("identifiant", "mot de passe", "incorrect", "invalide", "authentif")):
        return ShopError(
            "izly_bad_credentials",
            "Izly refused the configured login (IZLY_USER / IZLY_PASS). Check them.", izly_message=text)
    if any(w in t for w in ("bloqu", "verrouill", "suspendu")):
        return ShopError("izly_account_locked",
                         "The Izly account is blocked or locked. It has to be unblocked on the Izly side.",
                         izly_message=text)
    return ShopError("izly_error", f"Izly refused the payment: {text or 'unknown reason'}",
                     izly_message=text)


def classify_checkout(text):
    """Map the WooCommerce checkout failure notice to a ShopError."""
    t = (text or "").lower()
    if "déjà" in t or "un seul" in t or "une seule" in t:
        return ShopError("already_ordered",
                         "The site refused the order: only one 1 EUR meal per order and per student.",
                         site_message=text)
    if "stock" in t or "épuisé" in t or "plus disponible" in t:
        return ShopError("out_of_stock", "This menu is no longer available (sold out).", site_message=text)
    if "izly" in t and ("lié" in t or "compte" in t):
        return ShopError("izly_not_linked",
                         "The Izly account is not linked to the Crous & Go account. Link it once from the "
                         "site (My account, Izly connection).", site_message=text)
    return ShopError("checkout_failed", f"The site refused the checkout: {text}",
                     site_message=text)
