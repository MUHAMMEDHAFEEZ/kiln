#!/usr/bin/env bash
# Opens the Kiln launcher (bound to Super+K). Starts Kiln first if it is not running.
pid=$(cat "${KILN_CACHE:-$HOME/.cache/kiln}/app.pid" 2>/dev/null)
if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
  kill -USR1 "$pid"
else
  "$(cd "$(dirname "$0")" && pwd)/start.sh"
fi
