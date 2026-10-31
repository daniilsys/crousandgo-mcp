"""Paths, constants and env loading.

Secrets come from the environment, or from ~/.config/crousandgo/.env / ./.env (first one wins,
real env vars win over both). They are never returned by a tool and never logged.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # project root (an editable install keeps the .env there)
CONFIG_DIR = Path(os.environ.get("CROUSGO_CONFIG_DIR", "~/.config/crousandgo")).expanduser()

SHOP = "https://crousandgo.crous-strasbourg.fr"
BASE = SHOP + "/fonderie"
STORE_API = BASE + "/wp-json/wc/store/v1"

ZONE_FONDERIE = "4"
MENU_CATEGORY = "menu-etudiant"
MENU_PRICE_CENTS = 100  # the 1 EUR meal; anything else is refused


def load_env():
    for f in (CONFIG_DIR / ".env", ROOT / ".env"):
        if not f.is_file():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


load_env()

SESSION = Path(os.environ.get("CROUSGO_SESSION", CONFIG_DIR / "session.json")).expanduser()
AUDIT = Path(os.environ.get("CROUSGO_AUDIT", CONFIG_DIR / "audit.jsonl")).expanduser()


def secret(name):
    return os.environ.get(name, "").strip()


def write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
