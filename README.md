<div align="center">

# crousandgo-mcp

**The Crous & Go 1 € student menu, as tools an AI agent can use.**<br>
See what's published, get asked which variant you want, and let the agent order and pay with Izly — with you in the loop.

<p>
  <img alt="python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="mcp" src="https://img.shields.io/badge/MCP-server-6E56CF">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-2EA44F">
  <img alt="status" src="https://img.shields.io/badge/status-unofficial-orange">
</p>

</div>

<br>

<table>
<tr>
<td width="50%" valign="top">

### What it does

- Lists the menus published on Crous & Go (Fonderie, Mulhouse): date, midi or soir, plats, side options, stock.
- Orders one for you: cart, checkout, then the Izly payment, with your own account.
- Asks you every evening which variant you want, once per target date, and says nothing when you already ordered.
- Tells the agent *why* when something fails, in plain words it can relay: "Izly balance too low to pay the 1 EUR menu…".

</td>
<td width="50%" valign="top">

### What it never does

- Order without a go: `order_menu` is a dry run unless the agent passes `dry_run=False`, and a kill switch refuses real orders until you enable them.
- Pay more than 1 €: any other price, or a cart that isn't exactly one 1 € menu, aborts.
- Order twice for a date, or go over the per-process order budget.
- Send your credentials anywhere but Crous & Go and Izly, or return them from a tool.

</td>
</tr>
</table>

> Unofficial. Not affiliated with CROUS or Izly. It drives the public shop and the Izly payment page with your own account, the way you would by hand. The rule stays: one 1 € meal per order and per student.

## Install

```bash
git clone https://github.com/daniilsys/crousandgo-mcp && cd crousandgo-mcp
./install.sh
```

It creates the virtualenv, installs the package (`crousandgo` and `crousandgo-mcp` commands) and Chromium for Playwright, and prints how to register the MCP server in your client (command `.venv/bin/crousandgo-mcp`). The agent skill is in `skills/crousandgo-order`. Then fill in `~/.config/crousandgo/.env`:

```
CROUS_USER=...        # your Crous & Go login
CROUS_PASS=...
IZLY_USER=...         # your Izly login
IZLY_PASS=...
CROUSGO_ENABLE_WRITES=1   # kill switch: real orders are refused unless this is 1
CROUSGO_MAX_ORDERS=1      # real orders per server process
CROUSGO_LANG=en           # language of the evening question: en or fr
IZLY_TOPUP_EUR=5          # top-up amount: 5, 10, 15 or 20 (tool topup_izly)
IZLY_TOPUP_CARD=2958      # saved card to use (part of its label in Izly); empty = first one
```

Top-up: when an order fails with `insufficient_funds`, the agent asks you, then calls `topup_izly`, which uses a card you saved once on mon-espace.izly.fr (Recharger, carte bancaire). Izly requires the bank's strong authentication for card top-ups: approve it in your bank app while the tool waits (`IZLY_TOPUP_WAIT_S`). Then the agent redoes the order.

and log in once: `crousandgo login`. The Izly account must be linked to your Crous & Go account (My account, Connexion à Izly) and funded.

## Use it

```bash
crousandgo menus                                     # what is published
crousandgo order 2026-11-02 poulet gateau gateau     # dry run: checks the choices, orders nothing
crousandgo order 2026-11-02 poulet gateau gateau --yes   # real order, paid with Izly
crousandgo watch 2026-11-03 poulet gateau gateau     # wait for 13:00 the day before, then order
crousandgo ask                                       # the 20:00 question, or nothing
```

Choices are matched by substring, accents and case ignored. `--service soir` picks the evening menu.

With an agent, just talk to it: *"commande-moi le menu de demain, poulet, fruit et compote"*. The skill makes it validate with a dry run and ask when something is ambiguous.

## Tools

| Tool | |
|---|---|
| `list_menus` | published menus, plats, side options, stock, when ordering opens |
| `get_cart` | current cart |
| `get_orders` | recent orders with status, what was ordered (plat, side options), pickup date/time/place and an `upcoming` flag |
| `clear_cart` | empty the cart |
| `order_menu` | order a menu. `dry_run=True` by default, `reason` is mandatory |
| `topup_izly` | top up Izly with the saved card after the user agreed; waits for their approval at the bank. `dry_run=True` by default, `reason` is mandatory |

Every tool returns `{ok, ...}`. On `ok: false` there is a stable `code` and an English `message` the agent can relay in your language:
`insufficient_funds`, `izly_bad_credentials`, `izly_not_linked`, `menu_not_open`, `menu_not_published`, `out_of_stock`, `already_ordered`, `bad_choice`, `unexpected_price`, `payment_not_confirmed`, `session_expired`, `site_unreachable`, `writes_disabled`, `budget_exhausted`, `topup_card_not_found`, `topup_needs_verification`, `topup_not_confirmed`, `topup_refused`.

## Safety

- **Dry run by default.** The agent must pass `dry_run=False` explicitly, and the skill only lets it do so after your answer for that exact date.
- **Kill switch.** `CROUSGO_ENABLE_WRITES` must be `1`. `crousandgo-mcp --read-only` doesn't even register the write tools.
- **Budget.** `CROUSGO_MAX_ORDERS` real orders per server process (default 1).
- **Price cap.** The menu must cost exactly 1 € and the cart must hold only that item before checkout.
- **No silent success.** A payment counts only when the WooCommerce order reaches "En cours" or "Terminée". Otherwise the agent gets `payment_not_confirmed` and is told to check `get_orders` before retrying.
- **Audit log.** Every dry run and order is appended to `audit.jsonl` with its `reason`.
- **Secrets.** Credentials come from the environment or a `.env` (keep it `chmod 600`). They are never returned by a tool or logged. `session.json` holds Crous & Go cookies: treat it like a password.

## How it works

The shop is WordPress + WooCommerce.

- Catalogue and cart: the public Store API (`/wp-json/wc/store/v1`).
- Add to cart: the product page's own form POST (variation, pickup zone, date and slot).
- Checkout: `?wc-ajax=checkout`, which answers with the Izly redirect.
- Payment: Chromium (Playwright) fills the Izly page with your credentials. A refusal comes back in the page's `.error` block and is mapped to a code above.
- Login: Chromium logs in with `CROUS_USER` / `CROUS_PASS` and saves the cookies to `session.json`; it is replayed with `httpx` and renewed automatically when it expires. `crousandgo login --manual` opens a visible browser instead.

## Run it on a server

Everything is headless. Copy the folder (and the `.env`), run `./install.sh`, then `crousandgo login`. For the "ask every evening, order on reply" setup, see [docs/SCHEDULING.md](docs/SCHEDULING.md). To expose the server over HTTP: `crousandgo-mcp --http 8766` — there is no auth on that endpoint, keep it on a private network.

## Repository

```
src/crousandgo/
  server.py    MCP server (FastMCP, stdio or HTTP)      -> crousandgo-mcp
  cli.py       command line                             -> crousandgo
  tools.py     the agent-facing tools, guards, evening question
  client.py    HTTP client: catalogue, cart, checkout, orders
  auth.py      Chromium login, session.json
  izly.py      Izly payment page
  errors.py    error codes and messages
  config.py    paths, constants, env loading
skills/        agent skill
docs/          scheduling
tests/         offline tests (python -m unittest discover -s tests)
pyproject.toml
```

## Status

Used for real: login, add to cart, checkout, Izly payment and order confirmation were run end to end on a live account. The evening question and the reply-to-order flow depend on your agent setup and are described, not guaranteed, in [docs/SCHEDULING.md](docs/SCHEDULING.md). The "soir" menu pickup slot is assumed to be the same as midi. If the shop changes its markup, the HTML-parsing parts (checkout form, orders table) are the first thing to break.

## License

MIT. See [LICENSE](LICENSE).
