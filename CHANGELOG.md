# Changelog

## 1.2.0 (2026-10-25)

* **Updates**: Kiln checks GitHub for new releases and can install them for you (`updates.mode`: notify, auto or
  off; Settings → General; `kiln update`). The old version is kept and put back if the new one does not start.
* The Kiln app icon and the accent colour of GTK apps (GNOME Settings, Files, Kiln Settings) follow the theme.
* The Ubuntu Dock is kept on the left (`dock.position`; Settings → General).
* Clicking the clock card opens a prayer-times popup instead of the system monitor.
* Projects are found by a faster scanner that shows its progress ("Looking for projects…", "No git projects found"
  with a button to choose folders) and runs at low priority.
* Terminal: **▾** next to **+** opens a tab in home, desktop, another project or any folder; the buttons under the
  terminal follow the folder of the active tab.
* Light mode for older computers (`performance`, automatic below 3 cores or 4 GB): about half the idle CPU.

## 1.1.0 (2026-10-25)

* **Accounts**: connect Todoist, Trello, Linear, Asana, Jira, Notion, ClickUp, Slack, any iCal calendar
  (Google, Outlook, Apple) and Lark from Settings → Accounts or `kiln connect`. Their tasks appear in Today and
  their meetings in Agenda, and tasks can be ticked off from Kiln. Details are kept in a private file
  (see `docs/ACCOUNTS.md`).
* Agenda is no longer Lark-only, and shows a clear way to connect a calendar.
* Demo mode and screenshots tool (`tools/demo.py`), tests (`python3 -m unittest discover -s tests`),
  README screenshots and a tour GIF.
* Hardening: web links only (no `file:` or custom schemes), video-call links matched by host, limits on repeating
  calendar events, and the accounts cache is private to you.
* The installer no longer carries the migration from the project's earlier name.
* Terminal: split panes, new tabs open in the current project, and the buttons under it come from the
  project's own files.
* Work bar: a tests button (run, result, output), and Overview / Coding / Focus modes with
  `Super+J`, `kiln mode` and `kiln test`.
* Desktop icons: a dot for recently changed items and "Group icons by type".
* Dock cards for big screens: Git, System, Running, Today (calendar, Hijri date, prayer times) and Notes,
  placed in the free space beside the terminal.
* Tests card (status, run button, log tail, project scripts as a fallback); the Running card is now optional.
* Overview: the clock card shows the Hijri date and next prayer instead of CPU/RAM.
* Coding mode: Git, Tests and Notes stacked beside a wide terminal.
* Focus mode: dimmed screen, solid terminal in larger type, timer pill in the header.

## 1.0.0 (2026-10-21)

First public release.

* Desktop cards: Today (personal list, project tab with Claude Code tasks, Lark tasks), Agenda,
  GitHub, Projects, clock and CPU/RAM strip.
* Desktop terminal with tabs and command buttons, Focus timer, Now Playing.
* Work bar: open-project detection and switcher, git / pull request / CI status, running dev
  servers, services and Docker containers.
* Desktop icons with per-folder colours and symbols.
* Super+K launcher.
* Top bar and lock screen extension (status chips, now playing, focus, prayer times, progress).
* Eight themes with generated paper-cut wallpapers; user themes and wallpapers.
* `kiln` command, `config.json` settings, installer with restore point, uninstaller.
* Kiln Settings: a graphical setup program (themes gallery, general, projects, integrations,
  lock screen and prayer, health check) that opens after the first install.
* Responsive layout: follows the primary monitor's size and re-arranges when screens change.
* Optional YouTube-Music-only Now Playing (card and lock screen).
