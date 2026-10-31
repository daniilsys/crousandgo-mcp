"""crousandgo-mcp command line.

    crousandgo login [--manual]      # create/refresh session.json (headless with CROUS_USER/CROUS_PASS)
    crousandgo menus                 # what is published right now
    crousandgo ask [--no-mark]       # 20:00 question text, or nothing (already ordered / already asked)
    crousandgo order DATE PLAT P1 P2 [--service midi|soir] [--yes]
                                 # dry run unless --yes (needs CROUSGO_ENABLE_WRITES=1)
    crousandgo watch DATE PLAT P1 P2 [--service ...]
                                 # sleep until 13:00 the day before, then order as soon as the menu appears
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta

from . import auth
from . import tools
from .errors import ShopError


def show(res):
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


def watch(a):
    opens = tools._opens_at(a.date)
    wake = opens - timedelta(seconds=30)
    while (left := (wake - datetime.now(tools.TZ)).total_seconds()) > 0:
        time.sleep(min(left, 60))
    deadline = datetime.now(tools.TZ) + timedelta(minutes=30)
    while datetime.now(tools.TZ) < deadline:
        res = tools.order_menu(a.date, a.plat, a.p1, a.p2, reason="cli watch", service=a.service, dry_run=False)
        if res.get("code") not in ("menu_not_open", "menu_not_published", "site_unreachable"):
            return show(res)
        time.sleep(5)
    return show({"ok": False, "code": "menu_not_published", "message": "The menu never appeared within 30 minutes."})


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login"); s.add_argument("--manual", action="store_true")
    sub.add_parser("menus")
    s = sub.add_parser("ask"); s.add_argument("--no-mark", action="store_true")
    for name in ("order", "watch"):
        s = sub.add_parser(name)
        s.add_argument("date"); s.add_argument("plat"); s.add_argument("p1"); s.add_argument("p2")
        s.add_argument("--service", default="midi")
        if name == "order":
            s.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    try:
        if a.cmd == "login":
            auth.login(manual=a.manual)
            print("OK, session saved.")
            return 0
        if a.cmd == "menus":
            return show(tools.list_menus())
        if a.cmd == "ask":
            q = tools.evening_question(mark=not a.no_mark)
            if q:
                print(q)  # empty stdout = nothing to send (cron --no-agent stays silent)
            return 0
        if a.cmd == "order":
            return show(tools.order_menu(a.date, a.plat, a.p1, a.p2, reason="cli", service=a.service,
                                         dry_run=not a.yes))
        return watch(a)
    except ShopError as e:
        return show(e.as_result())


if __name__ == "__main__":
    sys.exit(main())
