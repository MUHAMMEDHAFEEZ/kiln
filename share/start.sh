#!/usr/bin/env bash
# Starts (or restarts) the Kiln desktop: cards, terminal, icons, music, focus and the work bar.
# It runs as its own systemd user service, so it never dies together with whatever launched
# it (a terminal, an IDE...) and systemd brings it back if it ever crashes.
#   logs: journalctl --user -u kiln      (or: kiln logs)
D="$(cd "$(dirname "$0")" && pwd)"
CACHE="${KILN_CACHE:-$HOME/.cache/kiln}"
UNIT=kiln
mkdir -p "$CACHE"

systemctl --user stop "$UNIT.service" 2>/dev/null
systemctl --user reset-failed "$UNIT.service" 2>/dev/null
if [ -f "$CACHE/app.pid" ]; then          # an instance started without systemd
  kill "$(cat "$CACHE/app.pid")" 2>/dev/null
fi
sleep 0.5

# /usr/bin/python3 on purpose: a virtualenv earlier in PATH usually has no GTK bindings
systemd-run --user --unit="$UNIT" --description="Kiln desktop" \
  -p Restart=on-failure -p RestartSec=2 \
  -E GDK_BACKEND=x11 -E PYTHONFAULTHANDLER=1 -E PYTHONUNBUFFERED=1 \
  -E DISPLAY="${DISPLAY:-:0}" ${XAUTHORITY:+-E XAUTHORITY="$XAUTHORITY"} \
  ${KILN_CONFIG:+-E KILN_CONFIG="$KILN_CONFIG"} ${KILN_CACHE:+-E KILN_CACHE="$KILN_CACHE"} \
  /usr/bin/python3 -X faulthandler "$D/app.py" >/dev/null 2>&1
exit 0
