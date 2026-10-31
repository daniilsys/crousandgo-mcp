"""Agent-facing tools for Crous & Go Fonderie.

Plain functions returning small JSON-able dicts. Failures come back as
{"ok": False, "code": ..., "message": <French text for the user>} instead of exceptions, so the
agent can relay `message` as-is. `order_menu` is the only write; it is guarded (dry run by
default, kill switch, order budget, 1 EUR price cap, audit log).

Env:
  CROUSGO_ENABLE_WRITES=1   real orders are refused unless set (kill switch)
  CROUSGO_MAX_ORDERS=1      max real orders per server process
  CROUSGO_AUDIT=<path>      JSONL audit log (default ~/.config/crousandgo/audit.jsonl)
"""
import functools
import json
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import izly
from client import Client, norm
from config import AUDIT, CONFIG_DIR, MENU_PRICE_CENTS, write_private
from errors import ShopError

TZ = ZoneInfo("Europe/Paris")
ORDERS_OPEN_AT = time(13, 0)  # the day before pickup
PAID_STATUSES = ("En cours", "Terminée")  # WooCommerce: processing / completed
_client = None
_orders_done = 0


def client():
    global _client
    if _client is None:
        _client = Client()
    return _client


def _audit(entry):
    entry["ts"] = datetime.now(TZ).isoformat(timespec="seconds")
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _safe(fn):
    """Turn ShopError into a result the agent can relay instead of a protocol error."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except ShopError as e:
            return e.as_result()
    return wrapper


def _opens_at(pickup_iso):
    d = datetime.fromisoformat(pickup_iso).date() - timedelta(days=1)
    return datetime.combine(d, ORDERS_OPEN_AT, TZ)


def _slim_cart(c):
    return {"items": [{"name": i["name"], "price_eur": int(i["prices"]["price"]) / 100,
                       "options": {d["name"]: d["value"] for d in i.get("item_data", [])}
                       | {v["attribute"]: v["value"] for v in i.get("variation", [])}}
                      for i in c["items"]],
            "total_eur": int(c["totals"]["total_price"]) / 100}


def _settled_status(c, order_id, wait_s=60):
    """Status of an order, polling while Izly's callback is still being processed."""
    import time
    status = None
    for _ in range(wait_s // 5):
        status = next((o["status"] for o in c.orders() if o["order_id"] == order_id), None)
        if status in PAID_STATUSES:
            break
        time.sleep(5)
    return status


# ---------- read tools ----------

@_safe
def list_menus() -> dict:
    """List the menus currently published on Crous & Go Fonderie (usually the 1 EUR meal, midi and
    soir, for the next open day): pickup date, plats, side options, stock and when ordering opens
    (13:00 the day before pickup, validate before 08:00 on pickup day). No login needed."""
    out = []
    for m in client().menus():
        out.append({"date": m["pickup_date"], "service": m["service"], "name": m["name"],
                    "price_eur": m["price_cents"] / 100, "in_stock": m["in_stock"],
                    "plats": m["plats"], "peripheriques": m["peripheriques"],
                    "ordering_opens_at": _opens_at(m["pickup_date"]).isoformat(timespec="minutes")
                    if m["pickup_date"] else None})
    return {"ok": True, "menus": out}


@_safe
def get_cart() -> dict:
    """Current cart of the logged-in user."""
    client().ensure_session()
    return {"ok": True, **_slim_cart(client().cart())}


@_safe
def get_orders() -> dict:
    """Recent orders with their status (e.g. 'Attente paiement', 'Terminée'); the 5 latest also carry
    their pickup_date (YYYY-MM-DD)."""
    return {"ok": True, "orders": client().orders(detail=5)[:10]}


@_safe
def clear_cart() -> dict:
    """Empty the cart. Does not cancel orders already created."""
    return {"ok": True, "removed": client().clear_cart()}


# ---------- evening question ----------

ASKED = CONFIG_DIR / "asked.json"


def evening_question(mark=True, today=None):
    """Text to send the user at 20:00, or None when nothing should be sent.

    Target = earliest published, in-stock menu strictly after today (so a weekend gives one question,
    for Monday). Nothing is sent when the user already has a paid order for that date, or when this
    target date was already asked about. `mark` records the question as asked."""
    today = today or datetime.now(TZ).date()
    menus = [m for m in client().menus()
             if m["pickup_date"] and m["pickup_date"] > today.isoformat() and m["in_stock"]]
    if not menus:
        return None
    target = min(m["pickup_date"] for m in menus)
    asked = json.loads(ASKED.read_text()) if ASKED.exists() else {}
    if target in asked:
        return None
    if any(o.get("pickup_date") == target and o["status"] in PAID_STATUSES
           for o in client().orders(detail=5)):
        return None
    day = menus[0]
    services = sorted({m["service"] for m in menus if m["pickup_date"] == target})
    d = datetime.fromisoformat(target)
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    lines = [f"🍽️ Menu à 1 € du {jours[d.weekday()]} {d:%d/%m} dispo ({' / '.join(services)}).",
             "Plats :"] + [f"{i}. {p.split(',')[0]}" for i, p in enumerate(day["plats"], 1)]
    lines += ["Périphériques (2) : " + ", ".join(day["peripheriques"]),
              "Réponds par ex. « midi, poulet, fruit, compote » (un seul repas à 1 € : midi ou soir). "
              "Commande possible jusqu'à 8h le jour J. « rien » si pas ce coup-ci."]
    if mark:
        asked[target] = datetime.now(TZ).isoformat(timespec="minutes")
        write_private(ASKED, json.dumps(asked))
    return "\n".join(lines)


# ---------- write tool ----------

def _pick(options, wanted, what):
    hits = [o for o in options if norm(wanted) in norm(o)]
    if len(hits) != 1:
        raise ShopError("bad_choice", f"{what} « {wanted} » invalide ou ambigu. Choix possibles : "
                        + " | ".join(options), options=options)
    return hits[0]


@_safe
def order_menu(date: str, plat: str, peripherique_1: str, peripherique_2: str, reason: str,
               service: str = "midi", dry_run: bool = True) -> dict:
    """Order the 1 EUR menu for pickup `date` (YYYY-MM-DD) at Fonderie, paid with Izly.
    `plat` and the two `peripherique_*` are matched by substring (accents/case ignored), e.g.
    plat='poulet', peripherique_1='gateau'. service is 'midi' or 'soir'. `reason` is mandatory
    (audit log). dry_run=True (default) only checks that the menu exists and the choices are valid.
    A real order needs the user's explicit go, then dry_run=False."""
    global _orders_done
    menus = [m for m in client().menus() if m["pickup_date"] == date and m["service"] == service]
    if not menus:
        opens = _opens_at(date)
        late = datetime.now(TZ) >= opens
        raise ShopError(
            "menu_not_published" if late else "menu_not_open",
            f"Le menu {service} du {date} n'est pas encore publié"
            + ("" if late else f" : les commandes ouvrent le {opens:%d/%m à %Hh%M}") + ".",
            opens_at=opens.isoformat(timespec="minutes"))
    m = menus[0]
    if not m["in_stock"]:
        raise ShopError("out_of_stock", f"Le menu {service} du {date} est épuisé.")
    if m["price_cents"] != MENU_PRICE_CENTS:
        raise ShopError("unexpected_price", f"Prix inattendu ({m['price_cents'] / 100} €), commande refusée.")
    p = _pick(m["plats"], plat, "Plat")
    p1 = _pick(m["peripheriques"], peripherique_1, "Périphérique 1")
    p2 = _pick(m["peripheriques"], peripherique_2, "Périphérique 2")
    vid = next((v["id"] for v in m["variations"] if v["plat"] == p), None)
    if vid is None:
        raise ShopError("bad_choice", "Aucune variation correspondant à ce plat.")
    plan = {"date": date, "service": service, "plat": p, "peripheriques": [p1, p2],
            "retrait": "Fonderie, 11:45-13:30", "total_eur": 1.0, "paiement": "Izly"}
    if dry_run:
        _audit({"action": "order_menu", "plan": plan, "reason": reason, "dry_run": True})
        return {"ok": True, "dry_run": True, "would": plan}
    if os.environ.get("CROUSGO_ENABLE_WRITES") != "1":
        raise ShopError("writes_disabled", "Les commandes réelles sont désactivées (CROUSGO_ENABLE_WRITES=1).")
    if _orders_done >= int(os.environ.get("CROUSGO_MAX_ORDERS", "1")):
        raise ShopError("budget_exhausted", "Nombre maximum de commandes atteint pour cette session.")
    c = client()
    try:
        c.clear_cart()  # the cart must contain only this menu
        cart = c.add_menu(m, vid, p, p1, p2)
        if int(cart["totals"]["total_price"]) != MENU_PRICE_CENTS or len(cart["items"]) != 1:
            c.clear_cart()
            raise ShopError("unexpected_price", "Le panier ne vaut pas 1,00 €, commande annulée.")
        izly_url = c.checkout()
        order = c.orders()[0]
        outcome = izly.pay(izly_url)
        status = _settled_status(c, order["order_id"])
        if status not in PAID_STATUSES:
            raise ShopError("payment_not_confirmed",
                            f"Le paiement n'est pas confirmé (commande {order['order_id']} : « {status} »). "
                            "Vérifier le mail Izly / Crous & Go avant de réessayer.", **outcome)
    except ShopError as e:
        _audit({"action": "order_menu", "plan": plan, "reason": reason, "ok": False, "code": e.code})
        raise
    _orders_done += 1
    _audit({"action": "order_menu", "plan": plan, "reason": reason, "ok": True, "status": status})
    return {"ok": True, "ordered": plan, "order_id": order["order_id"], "status": status,
            "message": f"Menu {service} du {date} commandé et payé (1,00 €), retrait Fonderie 11h45-13h30."}


READ_TOOLS = [list_menus, get_cart, get_orders]
WRITE_TOOLS = [clear_cart, order_menu]
