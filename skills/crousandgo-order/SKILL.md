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
- Every tool returns `ok`. When it is false, tell the user the `message` field in their language, then say what to do next.
- `order_menu` is the only write. Call it with `dry_run=False` only after the user has given their
  choices (plat and two side options, midi or soir) for that exact date.
- Never order twice for the same date. After `payment_not_confirmed`, `izly_timeout` or any unclear
  failure, call `get_orders` first: a recent order "En cours" or "Terminée" for that pickup date means
  it went through.

## What is my order?

`get_orders` returns each recent order with its menu name, the choices (plat and side options), the
pickup date, time and place, and `upcoming` (paid and still to collect). To answer "what is my order"
or "what do I pick up", report the `upcoming` ones: menu, plat, side options, date, time, place.
Say clearly when there is none.

## Ordering

1. `list_menus`: pick the date the user means (usually the next open one) and show plats and side options.
2. Get the choices. Ask a short question if something is missing or ambiguous. Never guess a plat.
3. `order_menu(..., dry_run=True, reason=...)` to validate the choices. Relay `message` on failure
   (a bad choice lists the valid ones).
4. With the user's go, the same call with `dry_run=False`.
5. One-line recap: menu, date, pickup, 1,00 EUR paid with Izly. On `insufficient_funds`, follow
   "Not enough Izly balance" below.

## Not enough Izly balance (`insufficient_funds`)

1. Do not top up on your own. Ask the user, in one message: the balance is too low, you can top up Izly by the configured amount with their saved card, and they will have to approve the payment in their bank app. Call `topup_izly` with dry_run=True first if you need to know the amount.
2. Only after their yes, tell them to watch their phone, then call `topup_izly` with dry_run=False and a short `reason`. It waits up to 5 minutes for their approval at the bank.
3. On success, redo the same `order_menu` (dry run, then real, same choices). Do not ask the order question again: the user already gave the choices.
4. On failure relay `message`. `topup_needs_verification` means the approval did not come in time: nothing was charged, offer to try again. After `topup_not_confirmed`, ask the user to check the Izly balance before any retry.
