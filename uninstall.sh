#!/usr/bin/env bash
# Removes Kiln and puts back the GNOME settings saved at install time.
#
#   ./uninstall.sh           remove Kiln, keep your lists and settings in ~/.config/kiln
#   ./uninstall.sh --purge   also delete ~/.config/kiln and ~/.cache/kiln
set -uo pipefail

SHARE="$HOME/.local/share/kiln"
CONF="$HOME/.config/kiln"
CACHE="$HOME/.cache/kiln"
UUID="kiln-shell@local"
EXT="$HOME/.local/share/gnome-shell/extensions/$UUID"
KEYPATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kiln-search/"
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

say() { printf '\033[38;2;217;119;87m==>\033[0m %s\n' "$*"; }

say "Stopping the widgets"
systemctl --user stop kiln 2>/dev/null
rm -f "$HOME/.config/autostart/kiln.desktop" "$HOME/.local/share/applications/kiln-settings.desktop" \
      "$HOME/.local/share/icons/hicolor/scalable/apps/kiln.svg" "$HOME"/.local/share/icons/hicolor/*/apps/kiln.png

say "Removing the shell extension and the Super+K shortcut"
gnome-extensions disable "$UUID" 2>/dev/null
exts="$(gsettings get org.gnome.shell enabled-extensions)"
exts="$(echo "$exts" | sed -e "s/, *'$UUID'//" -e "s/'$UUID', *//" -e "s/'$UUID'//")"
gsettings set org.gnome.shell enabled-extensions "$exts"
rm -rf "$EXT"
MK=org.gnome.settings-daemon.plugins.media-keys
list="$(gsettings get $MK custom-keybindings)"
list="$(echo "$list" | sed -e "s|, *'$KEYPATH'||" -e "s|'$KEYPATH', *||" -e "s|'$KEYPATH'||")"
[ "$list" = "[]" ] && list="@as []"
gsettings set $MK custom-keybindings "$list"
dconf reset -f "$KEYPATH"

if [ -d "$CONF/.restore" ]; then
  say "Restoring your previous GNOME settings"
  for p in /org/gnome/desktop/interface/ /org/gnome/desktop/background/ /org/gnome/shell/extensions/dash-to-dock/; do
    f="$CONF/.restore/$(echo "$p" | tr '/' '_').ini"
    [ -f "$f" ] && { dconf reset -f "$p"; dconf load "$p" < "$f"; }
  done
  if [ -f "$CONF/.restore/terminal-profile" ] && [ -f "$CONF/.restore/terminal.ini" ]; then
    p="/org/gnome/terminal/legacy/profiles:/:$(cat "$CONF/.restore/terminal-profile")/"
    dconf reset -f "$p"; dconf load "$p" < "$CONF/.restore/terminal.ini"
  fi
  if [ -f "$CONF/.restore/ding-was-enabled" ]; then
    gnome-extensions enable ding@rastersoft.com 2>/dev/null
  fi
fi

if [ -f "$HOME/.claude/settings.json" ] && grep -q "kiln/claude-hook.sh" "$HOME/.claude/settings.json"; then
  say "Removing the Claude Code hook"
  /usr/bin/python3 "$SHARE/claude-hook.py" remove
fi

say "Removing Kiln's files"
rm -rf "$SHARE"
rm -f "$HOME/.local/bin/kiln"
if [ $PURGE = 1 ]; then
  rm -rf "$CONF" "$CACHE"
  echo "  Your Kiln lists and settings were deleted."
else
  rm -rf "$CACHE"
  echo "  Your lists and settings are kept in $CONF (delete it, or run with --purge)."
fi
echo "  Done. Log out and back in to get the stock GNOME top bar and lock screen back."
