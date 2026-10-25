<div align="center">

# Kiln

**A warm, work-focused desktop for GNOME.**
Live cards for your day, a real terminal on the wallpaper, a work bar that knows what you are building,
and a lock screen that is actually useful, all in eight paper-cut themes.

![The eight Kiln themes](docs/screenshots/themes.jpg)

</div>

<!-- Add docs/screenshots/desktop.png and lockscreen.png, then uncomment:
![Kiln Dark desktop](docs/screenshots/desktop.png)
![Kiln lock screen](docs/screenshots/lockscreen.png)
-->

## What you get

| | |
|---|---|
| **Today** | Your own todo list, plus a tab for the project you are working on. That tab also shows the tasks of your latest Claude Code session there. Lark tasks show up too, if you use Lark. |
| **GitHub** | Notifications, your open pull requests with CI status, review requests and a 6-month contribution heatmap (through the `gh` CLI). |
| **Projects** | Your most recently touched git repositories, with language and uncommitted-change badges. |
| **Agenda** | Today's meetings from Lark Calendar (optional). |
| **Terminal** | A real terminal on the desktop, with tabs, the current git branch and one-click command buttons. |
| **Focus** | A Pomodoro timer (25 / 5 min; scroll to change the length) that counts today's sessions. |
| **Now Playing** | Any MPRIS player (Spotify, YouTube Music in a browser…), with artwork, seek bar and controls. |
| **Work bar** | Detects the projects you have open (VS Code windows, Claude Code sessions, terminals) and lets you switch between them. It shows the branch, ahead/behind, changes, pull request and CI status, and the last commit. It also lists your running dev servers, local services and Docker containers, which you can open, stop or hide. |
| **Desktop icons** | Folder icons in the theme's colours, with per-folder colour and symbol, drag and drop, multi-select, rename and trash. |
| **Launcher** (Super+K) | Search apps, projects, desktop items and recent files, do maths, run `>commands`, add todos and switch themes, or search the web. |
| **Top bar** | Workspace badge and desktop name, a search pill, the date on the right, and themed menus, quick settings, notifications, OSD and Alt+Tab. |
| **Lock screen** | A crisp wallpaper with your tasks, next meeting, GitHub and battery shown as counts only, never titles. It also has Now Playing with controls, a live Focus countdown, prayer times (12 calculation methods, offline) and day/week progress. |
| **Themes** | Kiln Dark, Paper Light, Sepia, Slate Ink, Nordic Frost, Graphite Moss, Graphite Lime and Violet Dusk. Each one sets GTK, icons, dock, GNOME Terminal, folders, top bar and widgets, with its own generated wallpaper. |

## Requirements

* **GNOME Shell 46**, for example **Ubuntu 24.04**, on Wayland or X11. Other versions are untested.
* Python 3 with GTK 3 and VTE. The installer checks for these:
  `python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 python3-gi-cairo python3-pil xdotool playerctl rsync`
* Optional:
  * [GitHub CLI](https://cli.github.com/) for the GitHub card and the PR/CI status.
  * [`lark-cli`](https://github.com/larksuite/cli) for Lark tasks and calendar.
  * Claude Code, for the project task view.

## Install

```bash
git clone https://github.com/MUHAMMEDHAFEEZ/kiln.git
cd kiln
./install.sh            # add --install-deps to apt-install anything missing
```

Then **log out and back in once** so GNOME loads the new top bar and lock screen.
Run `kiln doctor` at any time to check that everything is in place.

To update, `git pull` and run `./install.sh` again. Your lists and settings are never overwritten.

<details>
<summary>What the installer changes</summary>

* Copies the code to `~/.local/share/kiln`, the `kiln` command to `~/.local/bin` and the shell extension to `~/.local/share/gnome-shell/extensions/kiln-shell@local`.
* Creates `~/.config/kiln` with starter files. Existing files are kept.
* Adds an autostart entry and the **Super+K** shortcut. Your other custom shortcuts are kept.
* Turns off *Desktop Icons NG*, if it is on, because it covers the widgets and Kiln draws the icons itself. Skip this with `--keep-ding`.
* Applies the theme: GTK/icon theme, dark/light preference, wallpaper, dock colours and your default GNOME Terminal profile. Each of these can be switched off in the config.
* Saves your previous GNOME settings once to `~/.config/kiln/.restore`, so that `./uninstall.sh` can restore them.
* With `--claude-hook`, it adds a Claude Code *SessionStart* hook to `~/.claude/settings.json` (the file is backed up first). After that, Claude sees your project todos.

</details>

## Kiln Settings

Open **Kiln Settings** from the app menu (or run `kiln settings`). It opens by itself after the
first install.

* **Appearance**: a gallery of the themes with wallpaper previews. Apply one, use your own wallpaper
  for it, and choose what a theme may change outside Kiln.
* **General**: desktop name, 12/24-hour clock, web search engine.
* **Projects & files**: folders searched for projects, folders to skip, the folder shown as desktop icons.
* **Integrations**: GitHub, Lark, Claude Code (and its todo hook), YouTube-Music-only Now Playing.
* **Lock screen & prayer**: widgets on or off, wallpaper brightness and blur, prayer times by city,
  calculation method and language.
* **Health check**: the same check as `kiln doctor`, plus the log.

Changes are saved the moment you make them. Press **Restart Kiln** to load them into the widgets.

## Using Kiln

| | |
|---|---|
| **Super+K** | launcher |
| **Super+D** | show the desktop, Ubuntu's shortcut (the widgets stay visible) |
| Click the GitHub logo | refresh GitHub, projects and Lark now |
| Right-click anything | contextual actions (open in VS Code, copy path, stop server, hide…) |

The `kiln` command:

```text
kiln start | stop | restart | status | logs
kiln theme                 list themes          kiln theme sepia    switch theme
kiln todo add "Ship it"    personal list        kiln todo done 2
kiln tasks add "Fix login" list of the project in the current folder (-C <dir> for another)
kiln settings              open the Kiln Settings window
kiln config                open config.json     kiln doctor         check the installation
```

## Make it yours

* **Settings**: the Kiln Settings window, or by hand in `~/.config/kiln/config.json`. This sets the desktop name, the 12h/24h clock, which folders hold your projects, the web search engine, your city for prayer times, the lock-screen look, the integrations and which parts of the system a theme may change. See **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.
* **Themes**: copy one into `~/.config/kiln/themes.json` and change its colours, or write a new one. See **[docs/THEMES.md](docs/THEMES.md)**.
* **Wallpaper**: drop `~/.config/kiln/wallpapers/<theme>.jpg` (or `.png`) and run `kiln theme <theme>`.
* **Folders**: right-click a folder, then *Color* or *Icon*.
* **Terminal buttons**: `~/.config/kiln/quick-commands.txt`, one command per line.
* **Project subtitles**: `~/.config/kiln/project-names.txt`.

## Known limitations

* The layout is computed from your **primary monitor**: on 1920×1080 it is compact, and on bigger
  screens (1440p, ultrawide) the terminal grows and the desktop-icon grid gets more columns and
  rows. If you plug a different screen in, Kiln re-arranges itself a couple of seconds later.
* Only the primary monitor gets widgets.
* The widgets are X11 windows (through Xwayland) because GNOME has no layer-shell. That is why
  they stay put on *Show Desktop* and never appear in Alt+Tab.

## Privacy

Everything runs locally. Kiln has no account and no telemetry. GitHub data comes from your own
`gh` login, and Lark data from your own `lark-cli` login. The lock screen shows counts only
(for example "3 tasks left"), never task or meeting titles. Prayer times are calculated on your
machine.

## Troubleshooting

* **Widgets do not respond to clicks**: *Desktop Icons NG* is probably on. Run `gnome-extensions disable ding@rastersoft.com`.
* **Top bar or lock screen look like stock GNOME**: log out and back in once after installing. Then check `kiln doctor`.
* **Something looks wrong**: run `kiln logs`. If the widgets ever freeze for 4 s, the log shows where.
* **GitHub card says "connecting…"**: run `gh auth login`.

## Uninstall

```bash
./uninstall.sh          # keeps ~/.config/kiln;  --purge removes it too
```

This restores your previous theme, wallpaper, dock and terminal settings, re-enables
*Desktop Icons NG* if the installer turned it off, and removes the Claude Code hook.

## License

[MIT](LICENSE) © 2026 Mohammed Hafeez
