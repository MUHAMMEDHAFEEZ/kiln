# Changelog

## 1.4.0 (2026-11-19)

A terminal that suggests commands, a Cloudflare WARP card, pictures and files on new tasks, and a details window for every task.

* **Task details window**: a click on a task (board, Tasks mode, the Today card) opens a window on the desktop first, not the
  browser. It shows the status, priority, project, department, who it is assigned to, start date and deadline ("in 3 days",
  overdue in red), time spent, attachments, the **description** (Arabic text reads right to left), the progress notes and
  the LINK column, with the actions beside them: Start working / Stop timer, Mark completed, move to another status,
  **Open in Lark ↗**, Copy link, Copy description. Every task you open is a **tab**; drag the window by any empty part,
  scroll a long description (wheel, PageUp/PageDown), Ctrl+Tab switches tabs, Esc closes the tab. It stays open until you close it.
* **Terminal suggestions**: grey text after your cursor, like fish. It comes from your history (this folder first, then
  what you use most and last), then from the project: `package.json` scripts, Makefile targets, git branches and
  subcommands, folders for `cd`, Docker and Django commands, the usual commands of Go, Rust, Python and compose projects,
  a built-in list of everyday commands and the programs on your PATH, so an empty history still gets suggestions. **Tab** or **→** takes it, **Ctrl+→** one word, **Esc** hides it.
  **Ctrl+Space** asks an AI to finish the line, and  `# find files over 100 MB`  followed by Ctrl+Space turns a request into a
  command (Tab puts it on the line; nothing runs until you press Enter). The AI is a local model through Ollama; Claude is
  an option. Nothing is sent unless you ask. Commands that look like they hold a password, key or token are never
  remembered, suggested or sent. `terminal.suggestions`, Settings → Desktop. See `docs/TERMINAL.md`.
* Terminal, from kitty: **Ctrl+C** copies when text is selected and interrupts otherwise, **Ctrl+F** searches the terminal
  (Ctrl+Shift+F anywhere), **PageUp / PageDown** scroll the history at the prompt, **Ctrl +/-/0** change the text size, and
  a **cursor trail** follows the cursor when it jumps (`terminal.cursor_trail`).
* **New task** takes pictures and files: *Add…*, drop them on the window or paste a picture with Ctrl+V. Pictures go to the
  table's Images column, other files to DOCS (`images_field`, `files_field`). `kiln newtask --image shot.png`. Needs
  `lark-cli auth login --scope "docs:document.media:upload"` once. If only the upload fails the task is kept and
  *Retry upload* appears.
* **VPN**: a Cloudflare WARP chip in the work bar (click to switch on or off, right-click for details) and an optional
  **VPN** card with latency, loss, traffic and mode. Needs `warp-cli`. `vpn.chip`, `dock.cards`.
* The work bar's Running section no longer shows only a number for servers that do not fit: **+N more ▾** opens the list
  (click a dev server to open it).
* Security, terminal suggestions: names that come from a project (package.json scripts, Makefile targets, git branches, folders)
  are suggested only when they are plain words, so `build; rm -rf ~` as a script name is never offered; folder names given to
  the AI are plain too and Claude runs with no tools; an AI answer that pipes into a shell or wipes a disk is dropped; and
  a program that prints the "at the prompt" window title can no longer turn the suggestions on (the shell must be in front).
* Fixed: installing with `--no-apply` removed the shell extension's generated `stylesheet.css` and `logo.svg`, which left the
  top bar unstyled until the theme was applied again.

## 1.3.0 (2026-11-15)

A big release: a **Books mode** with a PDF library and an open-book reader, a Lark Base tasks board with a Tasks mode, time tracking,
and fixes for freezes and typing lag in the terminal.

* **Books mode**: a fifth mode for your PDFs. A library with covers, *Continue reading*, filters and a reading-statistics strip
  (today against a goal, streak, pages, the week), and a reader with continuous scrolling, zoom, search, a table of contents,
  bookmarks, four highlight colours with notes, sepia and night pages, a progress bar and links. Time and pages read are counted
  while you read, and an eye-break reminder comes after 30 minutes. Needs `gir1.2-poppler-0.18`. `kiln books`,
  `kiln mode books`, Settings → Desktop → Books. See `docs/BOOKS.md`.
* Books: **Set cover image…** in a book's right-click menu uses a picture of your choice as its cover.
* Books: a book's cover is its own first page; Kiln draws a cover (colours and picture by subject, title and author) only when that page is blank, or for every book with `books.covers: designed`.
* Books: an **open-book view** (the default): two pages side by side with the cover on its own, one spread at a time, with a
  page-turning animation (arrow keys, wheel, click or drag the page edge). `s` cycles it with two scrolling views; `books.view`.
* **Lark Base**: a new service and a **Tasks board** card with a tab per list (projects, your own tasks); what you are
  working on also shows in Today. See `docs/ACCOUNTS.md`.
* The board shows a status bar and filter in red (not started), yellow (ongoing) and green (completed). You can add a task
  (`+ New task`, `kiln newtask`), move a task between statuses (click its dot, or right-click) and complete it from Today.
* Freezes: git (the terminal's branch and footer buttons), the GPU reading and the notification switch of Focus mode no longer
  run on the main thread, so a big repository or a slow command cannot freeze the terminal. The service starts with
  `CANBERRA_DRIVER=null`, so a stuck sound server cannot freeze Kiln's windows. Focus dimming can be switched off
  (`modes.focus_dim`; off by itself on weak computers). A switch that takes over 0.4 s, and a stall over 2 s, leave a line in
  `kiln logs`.
* Typing lag in the terminal: keys no longer go through the system input method (ibus), which added about 150 ms to every
  key; `terminal.system_input_method` brings it back for Chinese, Japanese or Korean input. Every 20 s `kiln logs` gets a
  line with how fast typing was (median, 90%, worst) and which windows drew slowly.
* `kiln debug` prints what Kiln is doing right now (run it while something seems frozen) and its last log lines.
* **Time on tasks:** Start also starts a clock (shown in the work bar and on the card); finishing the task or starting
  another ends it. `kiln time` reports per project and task; the standup shows it. Start now tries every department of a task,
  never guesses a folder (it must be a git repository) and, when none matches, asks once and remembers the answer.
* **Start working** on a task (▶ on the card): moves it to Ongoing, opens a terminal in the project's folder on a branch named
  after the task and adds it to the project's todo list. **Standup** note (`kiln standup`, a button on the board) from what
  you finished, what is ongoing and what is next. **Notifications** for tasks assigned to you and deadlines today or tomorrow.
* Task titles copied into a project's todo list are flattened to one short line, marked `[Lark]`, and shown to Claude Code as
  untrusted data (`todo_for_claude: false` turns it off).
* A **Tasks mode** (fourth chip of the mode switcher): the whole screen is the board, a column per status. Drag a card to
  another column to change its status; the column it would land in lights up.
* The Agenda card can be turned off (`cards.agenda`); Today then uses its room and shows more tasks.
* Arabic text that fits its space now starts at the left like the rest of the row.

## 1.2.1 (2026-10-25)

* Updates are signed: Kiln installs a release only when it carries the maintainer's signature (checked with
  `ssh-keygen`), so a stolen GitHub account cannot push an update. `tools/release.sh` builds, signs and publishes
  a release; see `docs/RELEASING.md` and `SECURITY.md`.

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
