"""HTTP client for crousandgo.crous-strasbourg.fr/fonderie (WooCommerce).

Catalogue and cart reads use the public Store API; add-to-cart is the product page's own form
POST; checkout is `?wc-ajax=checkout`, which answers with the Izly redirect URL.
"""
import json
import re
import time
import unicodedata
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser

import httpx

from . import auth
from .config import BASE, CROUSTILLANT_API, FONDERIE_CUISINE, MENU_CATEGORY, SESSION, SHOP, STORE_API, ZONE_FONDERIE
from .errors import ShopError, classify_checkout

MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
          "octobre", "novembre", "décembre"]
Z = "crouslocation-zonerecuperation-"


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def text_of(html):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


class _FormParser(HTMLParser):
    """Collect the successful controls of the first <form> whose class contains `cls`."""

    def __init__(self, cls):
        super().__init__()
        self.cls, self.fields, self._in, self._sel = cls, {}, False, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and self.cls in (a.get("class") or "") and not self.fields and not self._in:
            self._in = True
        elif self._in and tag == "input" and a.get("name"):
            if a.get("type") in ("checkbox", "radio") and "checked" not in a:
                return
            self.fields[a["name"]] = a.get("value") or ""
        elif self._in and tag == "select" and a.get("name"):
            self._sel = a["name"]
            self.fields.setdefault(self._sel, "")
        elif self._in and tag == "option" and self._sel and "selected" in a:
            self.fields[self._sel] = a.get("value") or ""
        elif self._in and tag == "textarea" and a.get("name"):
            self.fields.setdefault(a["name"], "")

    def handle_endtag(self, tag):
        if tag == "select":
            self._sel = None
        elif tag == "form":
            self._in = False


def parse_form(html, cls):
    p = _FormParser(cls)
    p.feed(html)
    return p.fields


def pickup_date(product):
    """ISO pickup date of a menu product: its DLC attribute, else 'du lundi 2 novembre' in the name."""
    for attr in product.get("attributes", []):
        if norm(attr["name"]) == "dlc" and attr["terms"]:
            try:
                return datetime.strptime(attr["terms"][0]["name"], "%d/%m/%Y").date().isoformat()
            except ValueError:
                pass
    m = re.search(r"(\d{1,2})(?:er)? (" + "|".join(MONTHS) + ")", product["name"].lower())
    if m:
        today = date.today()
        d = date(today.year, MONTHS.index(m.group(2)) + 1, int(m.group(1)))
        return (d if d >= today else d.replace(year=today.year + 1)).isoformat()
    return None


class Client:
    def __init__(self):
        self.http = httpx.Client(timeout=httpx.Timeout(60.0), follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0 (crousandgo-mcp)"})
        self._load_cookies()
        self._checked = False

    # ---------- session ----------

    def _load_cookies(self):
        if not SESSION.exists():
            return
        for c in json.loads(SESSION.read_text()).get("cookies", []):
            self.http.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))

    def _request(self, method, url, **kw):
        last = None
        for i in range(3 if method == "GET" else 1):
            try:
                return self.http.request(method, url, **kw)
            except httpx.TransportError as e:  # slow site: timeouts, resets
                last = e
                time.sleep(2 * (i + 1))
        raise ShopError("site_unreachable",
                        "The Crous & Go site is not responding (it is often slow). Try again in a moment.",
                        detail=str(last))

    def ensure_session(self):
        if self._checked:
            return
        r = self._request("GET", BASE + "/mon-compte/")
        if "woocommerce-MyAccount-navigation" not in r.text:
            auth.login()
            self.http.cookies.clear()
            self._load_cookies()
            r = self._request("GET", BASE + "/mon-compte/")
            if "woocommerce-MyAccount-navigation" not in r.text:
                raise ShopError("session_expired", "Could not log in to Crous & Go.")
        self._checked = True

    # ---------- catalogue ----------

    def menus(self):
        r = self._request("GET", f"{STORE_API}/products", params={"per_page": 100, "category": MENU_CATEGORY})
        r.raise_for_status()
        out = []
        for p in r.json():
            attrs = {a["name"]: [t["name"] for t in a["terms"]] for a in p["attributes"]}
            out.append({
                "product_id": p["id"],
                "name": p["name"],
                "service": "soir" if "soir" in p["name"].lower() else "midi",
                "pickup_date": pickup_date(p),
                "price_cents": int(p["prices"]["price"]),
                "in_stock": p["is_in_stock"] and p["is_purchasable"],
                "plats": attrs.get("Choix menu", []),
                "peripheriques": attrs.get("Choix 1", []),
                "variations": [
                    {"id": v["id"], "plat": next((a["value"] for a in v["attributes"]
                                                  if a["name"] == "Choix menu"), None)}
                    for v in p.get("variations", [])],
                "url": p["add_to_cart"]["url"],
            })
        return out

    def week_menu(self, code=FONDERIE_CUISINE):
        """Upcoming daily menus from the public CROUStillant API: [{date, midi: [[plat..]], soir: [[..]]}].
        Formulas are separated by an "OU" entry in the plat list."""
        r = self._request("GET", f"{CROUSTILLANT_API}/restaurants/{code}/menu")
        r.raise_for_status()
        out = []
        for day in r.json().get("data", []):
            entry = {"date": datetime.strptime(day["date"][:10], "%d-%m-%Y").date().isoformat()
                     if day["date"][2] == "-" else day["date"][:10]}
            for repas in day.get("repas", []):
                if repas["type"] not in ("midi", "soir") or not repas.get("categories"):
                    continue
                formulas = [[]]
                for pl in sorted(repas["categories"][0].get("plats", []), key=lambda x: x["ordre"]):
                    if pl["libelle"].strip().upper() == "OU":
                        formulas.append([])
                    else:
                        formulas[-1].append(pl["libelle"])
                entry[repas["type"]] = [f for f in formulas if f]
            out.append(entry)
        return sorted(out, key=lambda d: d["date"])

    # ---------- cart ----------

    def cart(self):
        r = self._request("GET", f"{STORE_API}/cart")
        r.raise_for_status()
        self._nonce = r.headers.get("nonce")
        return r.json()

    def clear_cart(self):
        self.ensure_session()
        c = self.cart()
        for it in c["items"]:
            r = self._request("POST", f"{STORE_API}/cart/remove-item",
                              headers={"Nonce": self._nonce}, json={"key": it["key"]})
            r.raise_for_status()
        return len(c["items"])

    def add_menu(self, menu, variation_id, plat, p1, p2, heure_idx="0"):
        self.ensure_session()
        r = self._request("POST", menu["url"], data={
            "attribute_choix-menu": plat, "attribute_choix-1": p1, "attribute_choix-2": p2,
            Z + "zone-recuperation": ZONE_FONDERIE,
            Z + "date-recuperation": datetime.fromisoformat(menu["pickup_date"]).strftime("%d/%m/%Y"),
            Z + "heure-recuperation": heure_idx,
            "quantity": 1, "add-to-cart": menu["product_id"], "product_id": menu["product_id"],
            "variation_id": variation_id})
        errs = re.findall(r'<ul class="woocommerce-error".*?</ul>', r.text, re.S)
        if errs:
            raise ShopError("add_to_cart_failed", f"The site refused the add to cart: {text_of(errs[0])}",
                            site_message=text_of(errs[0]))
        c = self.cart()
        if not any(i["id"] == variation_id for i in c["items"]):
            raise ShopError("add_to_cart_failed", "The menu was not added to the cart (unknown reason).")
        return c

    # ---------- checkout ----------

    def checkout(self):
        """Submit the checkout; returns the Izly redirect URL. Creates a WooCommerce order."""
        self.ensure_session()
        page = self._request("GET", BASE + "/commander/")
        form = parse_form(page.text, "checkout")
        if "woocommerce-process-checkout-nonce" not in form:
            raise ShopError("checkout_failed", "Checkout page not found (empty cart?).")
        form.update({"payment_method": "izlyvl", "terms": "on", "terms-field": "1"})
        r = self._request("POST", BASE + "/?wc-ajax=checkout", data=form)
        try:
            body = r.json()
        except ValueError:
            raise ShopError("checkout_failed", "Unexpected response from the site at checkout.", status=r.status_code)
        if body.get("result") == "success" and body.get("redirect"):
            return body["redirect"]
        raise classify_checkout(text_of(body.get("messages", "")))

    # ---------- orders ----------

    def _order_detail(self, order_id):
        """Items, options and pickup of one order, from its page."""
        html = self._request("GET", f"{BASE}/mon-compte/view-order/{order_id}/").text
        items = []
        for row in re.findall(r'<tr class="woocommerce-table__line-item.*?</tr>', html, re.S):
            name = re.search(r'product-name">(.*?)(?:<strong class="product-quantity|<ul)', row, re.S)
            meta = {text_of(k).rstrip(" :"): text_of(v) for k, v in re.findall(
                r'<strong class="wc-item-meta-label">(.*?)</strong>\s*(.*?)</li>', row, re.S)}
            items.append({"name": text_of(name.group(1)) if name else None, "meta": meta})
        meta = items[0]["meta"] if items else {}
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})$", meta.get("Jour de récupération", ""))
        return {"pickup_date": f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None,
                "pickup_time": meta.get("Heure de récupération"),
                "pickup_place": meta.get("Zone de récupération"),
                "items": [{"name": i["name"],
                           "choices": {k: v for k, v in i["meta"].items() if k.startswith("Choix")}}
                          for i in items]}

    def orders(self, detail=0):
        """Recent orders; with detail=N, the first N also get `pickup_date` (ISO) from their page."""
        self.ensure_session()
        r = self._request("GET", BASE + "/mon-compte/orders/")
        out = []
        for row in re.findall(r'<tr class="woocommerce-orders-table__row.*?</tr>', r.text, re.S):
            cells = {k: text_of(v) for k, v in
                     re.findall(r'data-title="([^"]+)"[^>]*>(.*?)</t[dh]>', row, re.S)}
            num = re.search(r"view-order/(\d+)", row)
            out.append({"order_id": int(num.group(1)) if num else None,
                        "date": cells.get("Date"), "status": cells.get("État") or cells.get("Statut"),
                        "total": cells.get("Total")})
        for o in out[:detail]:
            o.update(self._order_detail(o["order_id"]))
        return out
