# Ask every evening, order on reply

The question is in English by default; set `CROUSGO_LANG=fr` for French.

The goal: at 20:00 the agent asks which variant of tomorrow's 1 EUR menu you want, and orders it when you answer.

It is split in two so the part that decides *whether* to message you is plain code, not an LLM:

1. **`crousandgo ask`** prints the question, or nothing. Run it from a scheduler that delivers stdout to a chat and stays silent on empty output.
   - one question per target date: a weekend gives a single question, for Monday, not one per evening;
   - nothing when you already have a paid order ("En cours" or "Terminée") for that date;
   - nothing when no menu is published or everything is out of stock.
2. **The agent** receives your answer in the chat, with the `crousandgo` MCP server and the `crousandgo-order` skill, validates it with a dry run, then orders.

State: `asked.json` in the config dir records which target dates were already asked about. `crousandgo ask --no-mark` previews without recording.

## Hermes (what the author runs)

```bash
# 1. register the MCP server in ~/.hermes/config.yaml (values must be quoted strings)
mcp_servers:
  crousandgo:
    command: /path/to/crousandgo-mcp/.venv/bin/python
    args: [/path/to/crousandgo-mcp/server.py]
    env: {CROUSGO_ENABLE_WRITES: "1", CROUSGO_MAX_ORDERS: "2"}
    lazy: true

# 2. the script the cron runs (must live in ~/.hermes/scripts/)
printf '#!/bin/bash\ncd /path/to/crousandgo-mcp && exec .venv/bin/python cli.py ask\n' > ~/.hermes/scripts/crousandgo_ask.sh
chmod 755 ~/.hermes/scripts/crousandgo_ask.sh

# 3. a no-agent cron: script stdout is delivered as is, empty stdout is silent
hermes cron create "0 20 * * *" --name "Menu Crous & Go 1 euro" \
  --no-agent --script crousandgo_ask.sh --deliver discord:<channel_id>
```

Copy `skills/crousandgo-order/SKILL.md` into the agent's skills directory so it knows the ordering rules.
Check the cron's `Next run` shows 20:00 in your timezone.

## Plain cron

`crousandgo ask` works with anything that can run a command at 20:00 and send its output:

```bash
0 20 * * * /path/to/crousandgo ask | grep . && <send it to your chat>
```

## Order without asking

`crousandgo watch 2026-11-03 poulet gateau gateau` sleeps until 13:00 the day before, then orders as soon as the menu is published. It needs `CROUSGO_ENABLE_WRITES=1`.
