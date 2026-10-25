#!/usr/bin/env bash
# Kiln installer: a warm, work-focused desktop for GNOME 46 (Ubuntu 24.04).
#
#   ./install.sh                 install / update for the current user
#   ./install.sh --install-deps  also install missing packages with apt (asks for sudo)
#   ./install.sh --keep-ding     leave "Desktop Icons NG" enabled (it hides Kiln's widgets)
#   ./install.sh --claude-hook   let Claude Code sessions see each project's Kiln todos
#   ./install.sh --no-apply      copy files only, do not change the look yet
#   ./install.sh --no-gui        do not open the Kiln Settings window at the end
#
# Nothing outside your home folder is touched (except packages with --install-deps).
# Your previous GNOME settings are saved once to ~/.config/kiln/.restore so that
# ./uninstall.sh can put them back.
set -euo pipefail

R="$(cd "$(dirname "$0")" && pwd)"
SHARE="$HOME/.local/share/kiln"
CONF="$HOME/.config/kiln"
CACHE="$HOME/.cache/kiln"
BIN="$HOME/.local/bin"
UUID="kiln-shell@local"
EXT="$HOME/.local/share/gnome-shell/extensions/$UUID"
KEYBASE="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
KEYPATH="$KEYBASE/kiln-search/"
KEYPATH2="$KEYBASE/kiln-mode/"

INSTALL_DEPS=0 KEEP_DING=0 CLAUDE_HOOK=0 APPLY=1 GUI=1
for a in "$@"; do
  case "$a" in
    --install-deps) INSTALL_DEPS=1 ;;
    --keep-ding) KEEP_DING=1 ;;
    --claude-hook) CLAUDE_HOOK=1 ;;
    --no-apply) APPLY=0 ;;
    --no-gui) GUI=0 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (see --help)"; exit 1 ;;
  esac
done

say()  { printf '\033[38;2;217;119;87m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }

[ "$(id -u)" -ne 0 ] || { echo "Run the installer as your normal user, not root."; exit 1; }
command -v gnome-shell >/dev/null || { echo "Kiln needs GNOME Shell."; exit 1; }
gnome-shell --version | grep -q ' 46' || warn "Kiln is built for GNOME Shell 46; $(gnome-shell --version) is untested."

# ------------------------------------------------------------------ dependencies
say "Checking dependencies"
missing=()
py() { /usr/bin/python3 -c "$1" >/dev/null 2>&1; }
py 'import gi; gi.require_version("Gtk","3.0"); from gi.repository import Gtk' || missing+=(python3-gi gir1.2-gtk-3.0)
py 'import gi; gi.require_version("Vte","2.91"); from gi.repository import Vte'  || missing+=(gir1.2-vte-2.91)
py 'import cairo; import gi; gi.require_foreign("cairo")'                       || missing+=(python3-gi-cairo)
py 'import PIL'                    || missing+=(python3-pil)
command -v xdotool   >/dev/null || missing+=(xdotool)
command -v playerctl >/dev/null || missing+=(playerctl)
command -v rsync     >/dev/null || missing+=(rsync)
if [ ${#missing[@]} -gt 0 ]; then
  if [ $INSTALL_DEPS = 1 ]; then
    sudo apt-get install -y "${missing[@]}"
  else
    echo "  Missing packages: ${missing[*]}"
    echo "  Install them with:  sudo apt install ${missing[*]}"
    echo "  (or run ./install.sh --install-deps)"
    exit 1
  fi
fi
command -v gh >/dev/null || warn "GitHub CLI (gh) not found: the GitHub card stays empty until you install it and run 'gh auth login'."

# ------------------------------------------------------------------ restore point (first install only)
mkdir -p "$CONF" "$CACHE"
if [ ! -d "$CONF/.restore" ]; then
  say "Saving your current GNOME settings to $CONF/.restore"
  mkdir -p "$CONF/.restore"
  for p in /org/gnome/desktop/interface/ /org/gnome/desktop/background/ \
           /org/gnome/shell/extensions/dash-to-dock/; do
    dconf dump "$p" > "$CONF/.restore/$(echo "$p" | tr '/' '_').ini"
  done
  prof="$(gsettings get org.gnome.Terminal.ProfilesList default 2>/dev/null | tr -d "'")" || prof=""
  if [ -n "$prof" ]; then
    echo "$prof" > "$CONF/.restore/terminal-profile"
    dconf dump "/org/gnome/terminal/legacy/profiles:/:$prof/" > "$CONF/.restore/terminal.ini"
  fi
  gsettings get org.gnome.shell enabled-extensions > "$CONF/.restore/enabled-extensions"
fi

# ------------------------------------------------------------------ files
say "Installing files"
mkdir -p "$SHARE" "$BIN" "$(dirname "$EXT")"
rsync -a --delete --exclude '__pycache__' "$R/share/" "$SHARE/"
install -m 755 "$R/bin/kiln" "$BIN/kiln"
rsync -a --delete "$R/shell-extension/$UUID/" "$EXT/"
for f in "$R"/config/*; do                       # starter files, never overwrite the user's own
  [ -e "$CONF/$(basename "$f")" ] || cp "$f" "$CONF/"
done
mkdir -p "$CONF/project-todos" "$CONF/wallpapers"

# ------------------------------------------------------------------ app menu entry + icon
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
install -m 644 "$SHARE/desktop/kiln.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/kiln.svg"
for px in 16 22 24 32; do      # the 2-layer mark is clearer at small sizes
  mkdir -p "$HOME/.local/share/icons/hicolor/${px}x${px}/apps"
  install -m 644 "$SHARE/desktop/icons/$px.png" "$HOME/.local/share/icons/hicolor/${px}x${px}/apps/kiln.png"
done
cat > "$HOME/.local/share/applications/kiln-settings.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Kiln Settings
Comment=Themes and settings for the Kiln desktop
Exec=/usr/bin/python3 $SHARE/settings.py
Icon=kiln
Categories=Settings;GTK;
Keywords=kiln;theme;desktop;widgets;
StartupNotify=true
DESKTOP
gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true

# ------------------------------------------------------------------ session integration
say "Adding Kiln to your session"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/kiln.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Kiln desktop
Comment=Desktop cards, terminal, icons and work bar
Exec=$SHARE/start.sh
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
NoDisplay=true
EOF

# Super+K opens the launcher, Super+J cycles Overview / Coding / Focus (added next to your own shortcuts)
MK=org.gnome.settings-daemon.plugins.media-keys
for kp in "$KEYPATH" "$KEYPATH2"; do
  list="$(gsettings get $MK custom-keybindings)"
  if [[ "$list" != *"$kp"* ]]; then
    if [[ "$list" == "@as []" || "$list" == "[]" ]]; then list="['$kp']"; else list="${list%]}, '$kp']"; fi
    gsettings set $MK custom-keybindings "$list"
  fi
done
KB="$MK.custom-keybinding:$KEYPATH"
gsettings set "$KB" name "Kiln search"
gsettings set "$KB" command "$SHARE/search.sh"
gsettings set "$KB" binding "<Super>k"
KB2="$MK.custom-keybinding:$KEYPATH2"
gsettings set "$KB2" name "Kiln mode"
gsettings set "$KB2" command "$BIN/kiln mode next"
gsettings set "$KB2" binding "<Super>j"

# Desktop Icons NG draws a full-screen window over the widgets and swallows their clicks
if [ $KEEP_DING = 0 ] && gnome-extensions info ding@rastersoft.com 2>/dev/null | grep -q "State: ACTIVE"; then
  say "Turning off Desktop Icons NG (Kiln draws the desktop icons itself; --keep-ding to skip)"
  touch "$CONF/.restore/ding-was-enabled"
  gnome-extensions disable ding@rastersoft.com || true
fi

# the shell extension (top bar + lock screen) is loaded by GNOME at the next login
exts="$(gsettings get org.gnome.shell enabled-extensions)"
if [[ "$exts" != *"$UUID"* ]]; then
  if [[ "$exts" == "@as []" || "$exts" == "[]" ]]; then exts="['$UUID']"; else exts="${exts%]}, '$UUID']"; fi
  gsettings set org.gnome.shell enabled-extensions "$exts"
fi
gnome-extensions enable "$UUID" 2>/dev/null || true

if [ $CLAUDE_HOOK = 1 ]; then
  say "Adding the Claude Code SessionStart hook"
  /usr/bin/python3 "$SHARE/claude-hook.py" add "$SHARE/claude-hook.sh"
fi

# ------------------------------------------------------------------ look
if [ $APPLY = 1 ]; then
  theme="$(cat "$CONF/current-theme" 2>/dev/null || echo kiln-dark)"
  say "Applying the ${theme} theme"
  /usr/bin/python3 "$SHARE/theme.py" "$theme"
fi

if [ $GUI = 1 ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && [ ! -e "$CONF/.welcomed" ]; then
  touch "$CONF/.welcomed"
  (setsid /usr/bin/python3 "$SHARE/settings.py" --welcome >/dev/null 2>&1 &)
fi

case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not in your PATH; add it to use the 'kiln' command." ;; esac
cat <<EOF

  Kiln is installed.
  • Log out and back in once so GNOME loads the new top bar and lock screen.
  • Open "Kiln Settings" from the app menu (or run 'kiln settings') to pick a theme and set it up.
  • Super+K opens the launcher. Super+J switches Overview / Coding / Focus.  'kiln theme' lists the themes.  'kiln doctor' checks everything.
  • Settings: $CONF/config.json   (docs/CONFIGURATION.md explains each one)
  • Remove it any time with ./uninstall.sh

EOF
