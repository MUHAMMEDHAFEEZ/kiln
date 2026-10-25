# Configuration

All settings live in **`~/.config/kiln/config.json`**. Open it with `kiln config`.

* **When changes apply.** Changes to the widgets apply after `kiln restart`. The top bar picks up
  changes at the next login; the lock screen re-reads them every time it locks.
* **Missing keys** fall back to the defaults below, so you can keep the file short.
* **Comments.** JSON has no comments. Put notes in a key such as `"_note"`; Kiln ignores unknown keys.

```json
{
  "desktop_name": "{Host}'s Desktop",
  "clock": "12h",

  "desktop_dir": "",
  "project_dirs": ["~/Desktop", "~/Projects", "~/code"],
  "project_exclude": [],

  "web_search": { "name": "DuckDuckGo", "url": "https://duckduckgo.com/?q={q}" },

  "location": { "city": "", "latitude": null, "longitude": null },
  "prayer": { "enabled": false, "method": "mwl", "asr": "standard", "language": "en" },

  "now_playing": { "youtube_music_only": false },
  "lock_screen": { "widgets": true, "brightness": 0.72, "blur": 0 },

  "terminal": { "project_commands": true },
  "modes": { "focus_mutes_notifications": true },
  "icons": { "recent_dot": true, "recent_hours": 48 },
  "dock": { "cards": ["git", "system", "day", "notes", "tests"] },
  "week_start": "sun",
  "performance": "auto",
  "updates": { "mode": "notify" },

  "integrations": { "github": true, "lark": false, "claude_code": true },
  "theme_apply": { "gtk": true, "dock": true, "gnome_terminal": true, "wallpaper": true }
}
```

## General

| Key | Default | Meaning |
|---|---|---|
| `desktop_name` | `"{Host}'s Desktop"` | Name in the top bar. `{Host}` is the capitalised machine name; `{host}` and `{user}` also work. |
| `clock` | `"12h"` | `"12h"` or `"24h"`. Used by the cards, the agenda and the lock screen. |
| `desktop_dir` | `""` | Folder whose contents the desktop icons show. Empty means your XDG Desktop folder. |
| `project_dirs` | `["~/Desktop"]` | Folders that are searched (a project may sit up to three folders down) for git repositories. These feed the Projects card, the launcher and the work bar. |
| `project_exclude` | `[]` | Folder names to skip while searching, e.g. `["OpenSource", "archive"]`. `node_modules`, `vendor` and `.cache` are always skipped. |
| `web_search` | DuckDuckGo | The launcher's last result. `{q}` is replaced by your search. Example: `{"name": "Google", "url": "https://www.google.com/search?q={q}"}`. |

## Prayer times (lock screen)

Prayer times only show once `prayer.enabled` is `true` **and** a latitude and longitude are set.
They are calculated on your machine and follow your system's time zone and daylight saving.

```json
"location": { "city": "Cairo", "latitude": 30.0444, "longitude": 31.2357 },
"prayer": { "enabled": true, "method": "egypt", "asr": "standard", "language": "ar" }
```

| Key | Values |
|---|---|
| `method` | `egypt`, `mwl` (Muslim World League), `isna`, `karachi`, `makkah` (Umm al-Qura), `gulf`, `kuwait`, `qatar`, `singapore`, `turkey`, `france`, `russia` |
| `asr` | `standard` (Shafi'i, Maliki, Hanbali) or `hanafi` |
| `language` | `ar` (الفجر …) or `en` (Fajr …) |

## Now Playing

| Key | Default | Meaning |
|---|---|---|
| `now_playing.youtube_music_only` | `false` | Show only YouTube Music: the [Pear Desktop](https://github.com/pear-devs/pear-desktop) app (`youtube-music`), or a browser tab on music.youtube.com. VLC, YouTube videos and other players are ignored. The card's *Open YouTube Music* button starts the desktop app when it is installed. |

## Terminal, modes and icons

| Key | Default | Meaning |
|---|---|---|
| `terminal.project_commands` | `true` | The buttons under the terminal come from the project shown in the work bar (git, plus its `package.json` scripts, Makefile targets, `manage.py`, Cargo, Go, Flutter...), followed by your own `quick-commands.txt`. `false` shows only `quick-commands.txt`. |
| `modes.focus_mutes_notifications` | `true` | Focus mode turns notification banners off and puts your previous setting back when you leave it (also after a crash or a restart). |
| `icons.recent_dot` | `true` | A dot on desktop items changed within `recent_hours`. A folder also counts when a project inside it has a recent commit. |
| `icons.recent_hours` | `48` | How long an item counts as recent. |

## Dock cards (big screens)

| Key | Default | Meaning |
|---|---|---|
| `dock.cards` | `git`, `system`, `day`, `notes`, `tests` | Which cards may use the free space beside the terminal, in the order they are placed: `git`, `system`, `day`, `notes`, `tests`, `services`. `services` (dev servers and containers) is off by default because the work bar already shows them. Cards that do not fit are left out, so a laptop screen shows none. |
| `dock.position` | `"left"` | Where Kiln keeps the Ubuntu Dock: `"left"`, `"right"`, `"bottom"` or `""` to leave it alone. Kiln puts it back at once if something moves it. |
| `performance` | `"auto"` | `"low"` makes Kiln lighter on older computers: background refreshes run about 2.5 times less often and the busiest-programs sample is taken less often. `"auto"` picks it on a computer with two cores or less, or under 4 GB of memory. `"normal"` never does. |
| `updates.mode` | `"notify"` | `"notify"`: a notification when a new release is out, you press Update. `"auto"`: install it by itself (not while you are in Focus mode). `"off"`: never check. Run `kiln update` any time, `kiln update --rollback` to go back one version. Updates come from the project's GitHub releases, only when signed with the maintainer's release key, and are installed the same way as `./install.sh`. |
| `week_start` | `"sun"` | First day of the week in the calendar: `"sun"`, `"sat"` or `"mon"`. |

The **Today** card shows prayer times only when `prayer.enabled` is on and a location is set (see
below); the Hijri date is the tabular calendar, so it can be a day away from the sighted date. Notes are
saved as plain files in `~/.config/kiln/notes/` (one per project, and `_scratch.md`).

Tests: the **tests** button in the work bar runs the project's test command through your login shell
(so tools from `nvm` or a virtualenv are found), with `CI=true`, for up to 15 minutes. The output is
saved in `~/.cache/kiln/test-<project>.log` (right-click the button to see it). Click it only on
projects whose tests are safe to run on this machine.

## Lock screen

| Key | Default | Meaning |
|---|---|---|
| `lock_screen.widgets` | `true` | `false` keeps GNOME's plain clock. |
| `lock_screen.brightness` | `0.72` | Wallpaper brightness behind the clock (0–1). |
| `lock_screen.blur` | `0` | Blur radius. GNOME's default is about 30. |

## Integrations

| Key | Default | Needs |
|---|---|---|
| `integrations.github` | `true` | [`gh`](https://cli.github.com/) logged in (`gh auth login`) |
| `integrations.lark` | `false` | [`lark-cli`](https://github.com/larksuite/cli), set up and logged in. Turn it on from Settings → Accounts. Every other service is connected there too, see [ACCOUNTS.md](ACCOUNTS.md). |
| `integrations.claude_code` | `true` | Claude Code. It reads `~/.claude/projects` to show each project's latest tasks. |

**Claude Code can also read your project todos.** For that, run `./install.sh --claude-hook`
once. It adds a *SessionStart* hook that hands Claude the open items of the project's list
and tells it to tick them off with `kiln tasks done <n>`.

## What a theme may change

`kiln theme` always restyles Kiln itself: the widgets, the top bar, the lock screen and the folder
colours. The keys below control the rest of the system.

| Key | What it changes |
|---|---|
| `theme_apply.gtk` | GTK and icon theme, light/dark preference |
| `theme_apply.dock` | Ubuntu Dock / Dash to Dock background and running dots |
| `theme_apply.gnome_terminal` | Colours of your **default** GNOME Terminal profile |
| `theme_apply.wallpaper` | Desktop wallpaper |

## Other files in `~/.config/kiln`

| File | What it is |
|---|---|
| `accounts.json` | The details of your connected accounts, readable only by you ([ACCOUNTS.md](ACCOUNTS.md)) |
| `todo.txt` | Your personal list (`[ ] item` / `[x] item`, one per line) |
| `project-todos/<repo>.txt` | Each project's list (`kiln tasks`) |
| `quick-commands.txt` | Buttons under the desktop terminal |
| `project-names.txt` | `repo | subtitle | #colour` labels for the Projects card |
| `running-hide.txt` | Process names kept off the work bar's *Running* list |
| `icon-styles.json` | Per-folder colour and symbol, set from the folder's right-click menu |
| `icons.txt` | Order of the desktop icons; drag icons to change it |
| `themes.json` | Your own themes (see [THEMES.md](THEMES.md)) |
| `wallpapers/` | Your own wallpaper per theme |
| `current-theme`, `current-project.txt` | State written by Kiln |
