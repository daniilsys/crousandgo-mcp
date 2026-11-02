"""Agent-facing tools for Crous & Go Fonderie.

Plain functions returning small JSON-able dicts. Failures come back as
{"ok": False, "code": ..., "message": <English text for the user>} instead of exceptions, so the
agent can relay `message` in the user's language. `order_menu` is the only write; it is guarded (dry run by
default, kill switch, order budget, 1 EUR price cap, audit log).

Env:
  CROUSGO_ENABLE_WRITES=1   real orders are refused unless set (kill switch)
  CROUSGO_MAX_ORDERS=1      max real orders per server process
  CROUSGO_AUDIT=<path>      JSONL audit log (default ~/.config/crousandgo/audit.jsonl)
  IZLY_TOPUP_EUR=5          amount of a topup_izly top-up: 5, 10, 15 or 20
  IZLY_TOPUP_WAIT_S=300     how long it waits for the user's approval at their bank
  CROUSGO_MAX_TOPUPS=1      max real top-ups per server process
  IZLY_TOPUP_CARD=<text>    saved card to use, matched against its label in Izly's list
                            (e.g. 2958); default: the first one
"""
import functools
import json
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import izly
from .client import Client, norm
from .config import AUDIT, CONFIG_DIR, MENU_PRICE_CENTS, secret, write_private
from .errors import ShopError

TZ = ZoneInfo("Europe/Paris")
ORDERS_OPEN_AT = time(13, 0)  # the day before pickup
PAID_STATUSES = ("En cours", "Terminée")  # WooCommerce: processing / completed
_client = None
_orders_done = 0
_topups_done = 0


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


def topup_wait():
    try:
        return max(30, int(os.environ.get("IZLY_TOPUP_WAIT_S", "300")))
    except ValueError:
        return 300


def topup_amount():
    try:
        return int(os.environ.get("IZLY_TOPUP_EUR", "5"))
    except ValueError:
        raise ShopError("bad_topup_amount", "IZLY_TOPUP_EUR must be 5, 10, 15 or 20.")


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
def get_week_menu(days: int = 7) -> dict:
    """Menu of the week at the Fonderie kitchen (Illberg), from the CROUStillant API: for each upcoming
    day (from today, up to `days` days), the midi and soir formulas, each a list of plats (formulas are
    alternatives). No login needed. Not the same as list_menus, which is the orderable 1 EUR menu."""
    today = datetime.now(TZ).date()
    limit = (today + timedelta(days=days)).isoformat()
    return {"ok": True, "days": [d for d in client().week_menu()
                                 if today.isoformat() <= d["date"] < limit]}


@_safe
def get_cart() -> dict:
    """Current cart of the logged-in user."""
    client().ensure_session()
    return {"ok": True, **_slim_cart(client().cart())}


@_safe
def get_orders() -> dict:
    """Recent orders with their status (e.g. 'Attente paiement', 'Terminée'). The 5 latest also carry
    what was ordered (menu name and choices: plat, side options), pickup date (YYYY-MM-DD), time
    and place, and `upcoming` (true when paid and still to be collected today or later). Use this to
    answer "what is my order / what do I have to pick up"."""
    today = datetime.now(TZ).date().isoformat()
    orders = client().orders(detail=5)[:10]
    for o in orders:
        o["upcoming"] = bool(o.get("pickup_date") and o["pickup_date"] >= today
                             and o["status"] in PAID_STATUSES)
    return {"ok": True, "orders": orders}


@_safe
def clear_cart() -> dict:
    """Empty the cart. Does not cancel orders already created."""
    return {"ok": True, "removed": client().clear_cart()}


# ---------- evening question ----------

ASKED = CONFIG_DIR / "asked.json"

# The question is delivered as is by the scheduler, so its language is configurable (CROUSGO_LANG=fr).
QUESTION = {
    "en": {
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        "title": "🍽️ 1 EUR menu for {day} {date} is available ({services}).",
        "plats": "Plats:",
        "sides": "Side options (2): ",
        "howto": "Reply e.g. \"midi, poulet, fruit, compote\" (one 1 EUR meal only: midi or soir). "
                 "Ordering is possible until 08:00 on the day. Reply \"rien\" to skip.",
    },
    "fr": {
        "days": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
        "title": "🍽️ Menu à 1 € du {day} {date} dispo ({services}).",
        "plats": "Plats :",
        "sides": "Périphériques (2) : ",
        "howto": "Réponds par ex. « midi, poulet, fruit, compote » (un seul repas à 1 € : midi ou soir). "
                 "Commande possible jusqu'à 8h le jour J. « rien » si pas ce coup-ci.",
    },
}


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
    t = QUESTION[os.environ.get("CROUSGO_LANG", "en")]
    lines = [t["title"].format(day=t["days"][d.weekday()], date=f"{d:%d/%m}", services=" / ".join(services)),
             t["plats"]] + [f"{i}. {p.split(',')[0]}" for i, p in enumerate(day["plats"], 1)]
    lines += [t["sides"] + ", ".join(day["peripheriques"]), t["howto"]]
    if mark:
        asked[target] = datetime.now(TZ).isoformat(timespec="minutes")
        write_private(ASKED, json.dumps(asked))
    return "\n".join(lines)


# ---------- write tool ----------

def _pick(options, wanted, what):
    hits = [o for o in options if norm(wanted) in norm(o)]
    if len(hits) != 1:
        raise ShopError("bad_choice", f'{what} "{wanted}" is invalid or ambiguous. Possible choices: '
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
            f"The {service} menu for {date} is not published yet"
            + ("" if late else f": ordering opens on {opens:%Y-%m-%d at %H:%M}") + ".",
            opens_at=opens.isoformat(timespec="minutes"))
    m = menus[0]
    if not m["in_stock"]:
        raise ShopError("out_of_stock", f"The {service} menu for {date} is sold out.")
    if m["price_cents"] != MENU_PRICE_CENTS:
        raise ShopError("unexpected_price", f"Unexpected price ({m['price_cents'] / 100} EUR), order refused.")
    p = _pick(m["plats"], plat, "Plat")
    p1 = _pick(m["peripheriques"], peripherique_1, "Side option 1")
    p2 = _pick(m["peripheriques"], peripherique_2, "Side option 2")
    vid = next((v["id"] for v in m["variations"] if v["plat"] == p), None)
    if vid is None:
        raise ShopError("bad_choice", "No variation matches this plat.")
    plan = {"date": date, "service": service, "plat": p, "peripheriques": [p1, p2],
            "pickup": "Fonderie, 11:45-13:30", "total_eur": 1.0, "payment": "Izly"}
    if dry_run:
        _audit({"action": "order_menu", "plan": plan, "reason": reason, "dry_run": True})
        return {"ok": True, "dry_run": True, "would": plan}
    if os.environ.get("CROUSGO_ENABLE_WRITES") != "1":
        raise ShopError("writes_disabled", "Real orders are disabled (set CROUSGO_ENABLE_WRITES=1).")
    if _orders_done >= int(os.environ.get("CROUSGO_MAX_ORDERS", "1")):
        raise ShopError("budget_exhausted", "Maximum number of orders reached for this session.")
    c = client()
    try:
        c.clear_cart()  # the cart must contain only this menu
        cart = c.add_menu(m, vid, p, p1, p2)
        if int(cart["totals"]["total_price"]) != MENU_PRICE_CENTS or len(cart["items"]) != 1:
            c.clear_cart()
            raise ShopError("unexpected_price", "The cart is not exactly 1.00 EUR, order cancelled.")
        izly_url = c.checkout()
        order = c.orders()[0]
        outcome = izly.pay(izly_url)
        status = _settled_status(c, order["order_id"])
        if status not in PAID_STATUSES:
            raise ShopError("payment_not_confirmed",
                            f"Payment not confirmed (order {order['order_id']}: \"{status}\"). "
                            "Check the Izly / Crous & Go email before retrying.", **outcome)
    except ShopError as e:
        _audit({"action": "order_menu", "plan": plan, "reason": reason, "ok": False, "code": e.code})
        raise
    _orders_done += 1
    _audit({"action": "order_menu", "plan": plan, "reason": reason, "ok": True, "status": status})
    return {"ok": True, "ordered": plan, "order_id": order["order_id"], "status": status,
            "message": f"{service.capitalize()} menu for {date} ordered and paid (1.00 EUR), pickup at the Fonderie 11:45-13:30."}


@_safe
def topup_izly(reason: str, dry_run: bool = True) -> dict:
    """Top up the Izly account with the bank card saved on it (amount and card come from the config).
    Use it after an order failed with insufficient_funds, and only once the user has agreed to the
    top-up in this conversation. The bank may need the user's approval in its own app while this runs
    (up to 5 minutes), so tell the user to expect it BEFORE calling with dry_run=False. dry_run=True
    (default) only reports what would be done. `reason` is mandatory (audit log). Afterwards, redo the order."""
    amount, card = topup_amount(), secret("IZLY_TOPUP_CARD")
    plan = {"amount_eur": amount, "card": card or "first saved card", "wait_s": topup_wait()}
    if dry_run:
        _audit({"action": "izly_topup", "plan": plan, "reason": reason, "dry_run": True})
        return {"ok": True, "dry_run": True, "would": plan}
    if os.environ.get("CROUSGO_ENABLE_WRITES") != "1":
        raise ShopError("writes_disabled", "Real actions are disabled (set CROUSGO_ENABLE_WRITES=1).")
    global _topups_done
    if _topups_done >= int(os.environ.get("CROUSGO_MAX_TOPUPS", "1")):
        raise ShopError("budget_exhausted", "Maximum number of top-ups reached for this session.")
    try:
        res = izly.topup(amount, card, timeout_s=plan["wait_s"])
    except ShopError as e:
        _audit({"action": "izly_topup", "plan": plan, "reason": reason, "ok": False, "code": e.code})
        raise
    _topups_done += 1
    _audit({"action": "izly_topup", "plan": plan, "reason": reason, "ok": True, **res})
    return {"ok": True, **res,
            "message": f"Izly topped up by {amount} EUR, balance is now {res['balance_eur']} EUR. Redo the order."}


READ_TOOLS = [list_menus, get_week_menu, get_cart, get_orders]
WRITE_TOOLS = [clear_cart, order_menu, topup_izly]
