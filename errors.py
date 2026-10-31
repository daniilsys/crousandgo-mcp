"""Failures the agent can relay to the user as-is.

Each carries a stable `code` and a French `message` written for the end user, so the agent
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
            "Solde Izly insuffisant pour payer le menu à 1 €. Il faut recharger le compte Izly, "
            "puis relancer la commande (la commande échouée est annulée automatiquement par le site).",
            izly_message=text)
    if any(w in t for w in ("identifiant", "mot de passe", "incorrect", "invalide", "authentif")):
        return ShopError(
            "izly_bad_credentials",
            "Izly a refusé l'identifiant ou le mot de passe configurés (IZLY_USER / IZLY_PASS). "
            "Il faut les vérifier.", izly_message=text)
    if any(w in t for w in ("bloqu", "verrouill", "suspendu")):
        return ShopError("izly_account_locked",
                         "Le compte Izly est bloqué ou verrouillé. Il faut le débloquer côté Izly.",
                         izly_message=text)
    return ShopError("izly_error", f"Izly a refusé le paiement : {text or 'raison inconnue'}",
                     izly_message=text)


def classify_checkout(text):
    """Map the WooCommerce checkout failure notice to a ShopError."""
    t = (text or "").lower()
    if "déjà" in t or "un seul" in t or "une seule" in t:
        return ShopError("already_ordered",
                         "Le site refuse la commande : un seul repas à 1 € par commande et par étudiant.",
                         site_message=text)
    if "stock" in t or "épuisé" in t or "plus disponible" in t:
        return ShopError("out_of_stock", "Ce menu n'est plus disponible (stock épuisé).", site_message=text)
    if "izly" in t and ("lié" in t or "compte" in t):
        return ShopError("izly_not_linked",
                         "Le compte Izly n'est pas lié au compte Crous & Go. Il faut le lier une fois "
                         "depuis le site (Mon compte, Connexion à Izly).", site_message=text)
    return ShopError("checkout_failed", f"Le site a refusé la validation de la commande : {text}",
                     site_message=text)
