# The desktop terminal

Beyond tabs and split panes (Ctrl+Shift+T / E / O / W) the terminal on your desktop has suggestions, search and a few
keys taken from kitty.

## Suggestions

Type and grey text appears after the cursor. It is only ever shown at a prompt, on the last line, with nothing after the
cursor, and never while a program such as `vim` or `less` is running.

| Key | |
|---|---|
| **Tab** or **→** | take the whole suggestion |
| **Ctrl+→** | take one word |
| **Esc** | hide it until you type again |
| **Ctrl+Space** | ask the AI to finish the line (a small ✦ marks an AI answer) |

Where a suggestion comes from, in this order:

1. **History**: what you ran before. The folder you are in counts most, then how often and how recently.
2. **The project**: `npm|pnpm|yarn|bun run <script>` from `package.json`, `make <target>`, `git checkout <branch>`, git,
   docker and `manage.py` subcommands, and folders for `cd`. Nothing is guessed before you have typed the first letter
   of the word. Projects with `go.mod`, `Cargo.toml`, `manage.py`, `pyproject.toml`, a compose file or a `Makefile` also
   get their usual commands (`go run .`, `go test ./...`, `cargo build`, `docker compose up -d`...).
3. **Everyday commands**: a built-in list (`ls` → `ls -la`, `git s` → `git status`, `docker ps`, `systemctl --user restart `...)
   and the programs on your `PATH` (`doc` → `docker`), so a fresh machine with no history is not blank.
4. **The AI**, only when you ask. Type `# find files bigger than 100 MB` and press Ctrl+Space: the grey text shows the command,
   **Tab** puts it on the line (replacing the request) and you press Enter when you are happy with it. Kiln never runs
   what the AI wrote. Its answers are filtered (nothing that pipes into a shell or wipes a disk) as a best effort, and Claude
   runs with no tools, in an empty private folder; the real safeguard is that nothing runs until you press Enter.

### The AI and your privacy

* Out of the box Kiln uses **a local model through Ollama** (`terminal.suggestions.model`, or the first one you have).
  Nothing leaves the computer. Without Ollama, Ctrl+Space says that AI is off.
* `terminal.suggestions.ai: "claude"` uses the `claude` CLI instead: the request goes to Anthropic.
* What the AI sees: the folder's name, the git branch, the names of the files in that folder, up to five recent commands
  from it and the line you typed. Never file contents.
* A command that looks like it holds a secret (a password, token, key, `Authorization:` header, a URL with a password,
  AWS/GitHub/Slack tokens) is **never remembered, suggested or sent**. A command that starts with a space is not
  remembered either, as in bash's `HISTCONTROL=ignorespace`.
* Kiln keeps `~/.config/kiln/commands.log` (private, mode 600): each command with the folder it ran in. Delete the file to
  make Kiln forget; it is trimmed by itself above 2 MB.
* Slow computers: a local 8-billion-parameter model can take several seconds, which is why the AI waits for Ctrl+Space.
  `terminal.suggestions.auto_ai: true` asks it by itself after a pause when history has nothing.

How it works: the terminal's bash starts from a generated `~/.cache/kiln/term-shell.bashrc` that loads your prompt and
`~/.bashrc` and then adds a `PROMPT_COMMAND` (logs the command and marks "a prompt is showing" with the window title
`kiln:prompt`) and a `PS0` (marks "a command is running"). `terminal.suggestions.enabled: false` goes back to the plain
setup.

## Keys

| Key | |
|---|---|
| **Ctrl+C** | copies when text is selected, interrupts otherwise |
| **Ctrl+F** (Ctrl+Shift+F anywhere) | search the terminal: Enter older match, Shift+Enter newer, Esc closes |
| **PageUp / PageDown** | scroll the history, at the prompt only (a running program gets the keys) |
| **Ctrl + / Ctrl - / Ctrl 0** | text size bigger / smaller / back to normal |

Ctrl+F only searches at the prompt, so `less` and `vim` still get theirs.

The **cursor trail** (`terminal.cursor_trail`) is a short comet behind the cursor when it jumps two cells or more.
