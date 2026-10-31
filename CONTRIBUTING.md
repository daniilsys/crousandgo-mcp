# Contributing

crousandgo-mcp is meant to stay small and easy to install. Before starting a large change, open an issue to discuss it.

## Pull requests

- One topic per PR. Don't mix a feature, a refactor and a behaviour change.
- Don't change existing CLI or tool behaviour without calling it out in the description.
- Never add anything that sends an order, a payment or a credential without the existing guards (dry run, kill switch, price cap).
- Install with `pip install -e .`, then run `python -m unittest discover -s tests` before pushing. Tests must not hit the network or need credentials.

## Commit messages

Format: `type: short description`

- `type` is one of `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.
- Lowercase, imperative mood, no trailing period, 72 characters max.
- Add a body when the *why* isn't obvious from the diff, separated by a blank line.
- One logical change per commit. No "wip", "fix typo" or "address review" commits: squash them before opening the PR.

Examples:

```
fix: decide payment success from the order status
feat: add the 20:00 evening question
docs: document the Hermes cron setup
```
