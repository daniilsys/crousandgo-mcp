#!/usr/bin/env bash
# Installs crousandgo-mcp: virtualenv, browser, `crousandgo` command. Prints how to register the MCP server.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${CROUSGO_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/crousandgo}"

echo "==> virtualenv + dependencies"
if [ ! -x "$HERE/.venv/bin/pip" ]; then
  PYBIN=""
  for c in "${CROUSGO_PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null &&
       "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' &&
       "$c" -m venv --clear "$HERE/.venv" >/dev/null 2>&1 &&
       [ -x "$HERE/.venv/bin/pip" ]; then
      PYBIN="$c"
      break
    fi
  done
  [ -n "$PYBIN" ] || { echo "Python 3.10+ with venv and pip is required." >&2; exit 1; }
fi
"$HERE/.venv/bin/pip" install -q --upgrade pip
"$HERE/.venv/bin/pip" install -q -r "$HERE/requirements.txt"
"$HERE/.venv/bin/playwright" install chromium

echo "==> config in $CONF"
mkdir -p "$CONF"; chmod 700 "$CONF"
if [ ! -f "$CONF/.env" ]; then
  cp "$HERE/.env.example" "$CONF/.env"; chmod 600 "$CONF/.env"
  echo "    fill in $CONF/.env (Crous & Go and Izly credentials)"
fi

echo "==> crousandgo command"
mkdir -p "$HOME/.local/bin"
ln -sfn "$HERE/bin/crousandgo" "$HOME/.local/bin/crousandgo"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "    add ~/.local/bin to your PATH";; esac

echo "==> MCP server"
cat <<M
Register the server in your MCP client, for example:
  command: $HERE/.venv/bin/python
  args:    $HERE/server.py
  env:     CROUSGO_ENABLE_WRITES=1, CROUSGO_MAX_ORDERS=1
The agent skill is in $HERE/skills/crousandgo-order (copy it where your agent reads skills).
M
echo "Done. Next: fill in the .env, then run: crousandgo login"
