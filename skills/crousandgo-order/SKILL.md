---
name: crousandgo-order
description: Order the 1 EUR Crous & Go student menu. Use when the user asks about the Crous & Go menu, answers the evening menu question, or wants to order a menu paid with Izly.
---

Use the `crousandgo` MCP tools (`list_menus`, `get_cart`, `get_orders`, `clear_cart`, `order_menu`).
If unavailable, explain how to enable the server and restart the agent. Reply in the user's language
(French if unsure). Pickup is at the Fonderie (Mulhouse), 11h45-13h30, paid with the user's Izly
account. One 1 EUR meal per order and per student: midi OR soir, not both. Orders must be validated
before 08:00 on pickup day.

## Rules

- Never show product ids, order ids or raw JSON. Show dates, menu names and choices.
- Every tool returns `ok`. When it is false, tell the user the `message` field as-is, then say what to do next.
- `order_menu` is the only write. Call it with `dry_run=False` only after the user has given their
  choices (plat and two side options, midi or soir) for that exact date.
- Never order twice for the same date. After `payment_not_confirmed`, `izly_timeout` or any unclear
  failure, call `get_orders` first: a recent order "En cours" or "Terminée" for that pickup date means
  it went through.

## Ordering

1. `list_menus`: pick the date the user means (usually the next open one) and show plats and side options.
2. Get the choices. Ask a short question if something is missing or ambiguous. Never guess a plat.
3. `order_menu(..., dry_run=True, reason=...)` to validate the choices. Relay `message` on failure
   (a bad choice lists the valid ones).
4. With the user's go, the same call with `dry_run=False`.
5. One-line recap: menu, date, pickup, 1,00 EUR paid with Izly. On `insufficient_funds`, tell the user
   to top up Izly and offer to retry.
