#!/usr/bin/python3
"""Widgets you build yourself: a note, the output of a command, saved scripts with run buttons, a list of shortcuts, a web page,
a picture.

    ~/.config/kiln/widgets.json   {"version": 1, "widgets": [{"id": "w1", "type": "note", "title": "Ideas", "text": "..."}, ...]}

They are cards like the others: Edit layout puts them on the Overview or the Coding mode, moves and resizes them.
A command widget runs *your* command as you (bash -c) every few seconds, so only put there what you would run yourself.
No GTK in here, so it can be tested without a display."""
import json
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~")
CONF = os.environ.get("KILN_CONFIG", f"{HOME}/.config/kiln")
FILE = f"{CONF}/widgets.json"
TYPES = {"note": "Note", "command": "Command output", "scripts": "Scripts", "links": "Shortcuts", "web": "Web page",
         "image": "Picture"}
ICONS = {"note": "check-circle", "command": "cpu", "scripts": "bolt", "links": "folder", "web": "net",
         "image": "sun"}                                                          # the glyph of the card's header
DESCRIPTIONS = {"note": "Free text you can keep on the desktop.",
                "command": "Runs a command again and again and shows what it prints.",
                "scripts": "Commands and scripts with run buttons: open or close apps, start and stop servers.",
                "links": "Web addresses, folders and commands, one click away.",
                "web": "A web page inside a widget: a dashboard, a board, a chat.",
                "image": "A picture, scaled to fit."}
MAX_WIDGETS, MAX_TITLE, MAX_TEXT, MAX_COMMAND, MAX_LINKS, MAX_PATH = 40, 40, 4000, 300, 12, 400
MAX_SCRIPT, MAX_SCRIPTS, WHERE = 2000, 12, ("background", "terminal")
MIN_ZOOM, MAX_ZOOM, MAX_REFRESH = 30, 300, 1440
MIN_INTERVAL, MAX_INTERVAL, DEFAULT_INTERVAL = 5, 3600, 30
OUTPUT_LIMIT = 4000
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def one_line(s, limit):
    s = " ".join(str(s or "").split())[:limit].strip()
    return s if s.isprintable() else ""


def clean_title(title, wtype="note"):
    return one_line(title, MAX_TITLE) or TYPES.get(wtype, "Widget")


def normalize_url(url):
    """An http(s) address from what you typed ('example.com' becomes https://example.com, 'localhost:3000' http://localhost:3000),
    or '' when it is not a web address. Nothing but http and https is ever loaded."""
    u = " ".join(str(url or "").split())
    if not u or re.search(r"\s", u) or len(u) > 500:
        return ""
    if not re.match(r"^https?://", u, re.I):
        if re.match(r"^[a-z][a-z0-9+.-]*:(?!\d{1,5}(?:[/?#]|$))", u, re.I):        # another scheme (javascript:, file:, ftp:...)
            return ""
        host = re.split(r"[:/?#]", u, maxsplit=1)[0].lower()
        local = host == "localhost" or host.endswith(".local") or re.match(r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", host)
        u = ("http://" if local else "https://") + u
    m = re.match(r"^https?://([^/?#@\s]+)([/?#].*)?$", u, re.I)
    if not m:
        return ""
    host = re.sub(r":\d{1,5}$", "", m.group(1))
    ok = host.lower() == "localhost" or re.fullmatch(r"\[[0-9A-Fa-f:]+\]", host) or \
        re.fullmatch(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+", host)
    return u if ok else ""


def clean_script(i):
    """One entry of a Scripts widget: {'label', 'command', 'mode': 'run' | 'service', 'where', 'cwd'} and, for a service,
    {'stop', 'check'}; or None. A 'run' entry runs once (in the background or in a terminal tab). A 'service' keeps running
    (a server, an app): Start and Stop, its state, its log. `stop` is the command that stops it (without one it is sent
    SIGTERM); `check` is a command that succeeds while it is running (without one Kiln follows the process it started)."""
    if not isinstance(i, dict):
        return None
    label = one_line(i.get("label"), 40)
    command = "".join(c for c in str(i.get("command") or "") if c in "\n\t" or c.isprintable())[:MAX_SCRIPT].strip("\n ")
    if not label or not command:
        return None
    mode = "service" if i.get("mode") == "service" else "run"
    stop = "".join(c for c in str(i.get("stop") or "") if c in "\n\t" or c.isprintable())[:MAX_SCRIPT].strip("\n ")
    out = {"label": label, "command": command, "mode": mode,
           "where": "background" if mode == "service" else (i.get("where") if i.get("where") in WHERE else "background"),
           "cwd": one_line(i.get("cwd"), MAX_PATH)}
    if mode == "service":                                         # a program that keeps running: Start / Stop and its state
        out["stop"] = stop
        out["check"] = one_line(i.get("check"), MAX_COMMAND)
    return out


# starting points for the entries of a Scripts widget (the editor's "Add from template"): what to open, close, start, stop
SCRIPT_TEMPLATES = [
    ("Open an app", {"label": "Open Firefox", "command": "gtk-launch firefox || firefox"}),
    ("Close an app", {"label": "Close Firefox", "command": "pkill -x firefox"}),
    ("Open a project in VS Code", {"label": "Open project", "command": "code ~/code/project"}),
    ("Open a folder", {"label": "Open Downloads", "command": "xdg-open ~/Downloads"}),
    ("Dev server (start / stop)", {"label": "Dev server", "mode": "service", "command": "npm run dev",
                                   "cwd": "~/code/project", "check": "ss -ltn | grep -q ':3000 '",
                                   "stop": "fuser -k 3000/tcp"}),
    ("Docker compose (up / down)", {"label": "Docker compose", "mode": "service", "command": "docker compose up -d",
                                    "cwd": "~/code/project", "check": "docker compose ps -q | grep -q .",
                                    "stop": "docker compose down"}),
    ("systemd user service", {"label": "My service", "mode": "service", "command": "systemctl --user start NAME",
                              "stop": "systemctl --user stop NAME", "check": "systemctl --user is-active --quiet NAME"}),
    ("Free a port", {"label": "Free port 3000", "command": "fuser -k 3000/tcp"}),
    ("Run the tests in a terminal", {"label": "Tests", "command": "pytest -q", "where": "terminal", "cwd": "~/code/project"}),
]


def parse_links(text):
    """[{'label', 'target'}] from lines like  Docs = https://example.com  or  Logs = ~/logs  or  Build = make -C ~/app."""
    items = []
    for line in str(text or "").splitlines():
        label, sep, target = line.partition("=")
        label, target = one_line(label, 40), one_line(target, MAX_COMMAND)
        if sep and label and target:
            items.append({"label": label, "target": target})
    return items[:MAX_LINKS]


def links_text(items):
    return "\n".join(f"{i['label']} = {i['target']}" for i in items or [])


def classify_target(target):
    """('url', address) for a web link, ('path', absolute path) for a file or folder, else ('command', the text)."""
    t = (target or "").strip()
    if re.match(r"^https?://[^\s/]+", t, re.I) and not re.search(r"\s", t):
        return "url", t
    if t.startswith(("/", "~")) and not re.search(r"[;&|<>$`\n]", t):
        return "path", os.path.expanduser(t)
    return "command", t


def run_command(command, timeout=10, limit=OUTPUT_LIMIT, cwd=None, script=False):
    """(ok, text): what the command printed (stdout and stderr, colours removed, shortened), or why it did not run.
    `script` keeps the lines of a multi-line script; otherwise the command is one line."""
    command = (str(command or "")[:MAX_SCRIPT].strip() if script else one_line(command, MAX_COMMAND))
    if not command:
        return False, "no command"
    cwd = os.path.expanduser(cwd) if cwd and os.path.isdir(os.path.expanduser(cwd)) else HOME
    try:
        r = subprocess.run(["/bin/bash", "-c", command], capture_output=True, text=True, errors="replace",
                           timeout=timeout, cwd=cwd, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout} s"
    except OSError as exc:
        return False, f"could not run it ({exc.strerror})"
    out = ANSI.sub("", (r.stdout or "") + (r.stderr or "")).replace("\r", "").rstrip()
    if len(out) > limit:
        out = out[:limit].rstrip() + "\n…"
    return r.returncode == 0, out or ("(no output)" if r.returncode == 0 else f"exit status {r.returncode}")


class Widgets:
    """Your widgets. Call save() after changes."""

    def __init__(self, path=None):
        self.path = path or FILE
        self.items = []
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        for w in (raw.get("widgets") if isinstance(raw, dict) else None) or []:
            clean = self._clean(w)
            if clean and clean["id"] not in {x["id"] for x in self.items} and len(self.items) < MAX_WIDGETS:
                self.items.append(clean)

    @staticmethod
    def _clean(w):
        """One widget with every field checked, or None."""
        if not isinstance(w, dict) or w.get("type") not in TYPES or not re.fullmatch(r"w\d{1,6}", str(w.get("id", ""))):
            return None
        t = w["type"]
        out = {"id": w["id"], "type": t, "title": clean_title(w.get("title"), t)}
        if t == "note":
            out["text"] = str(w.get("text") or "")[:MAX_TEXT]
        elif t == "command":
            out["command"] = one_line(w.get("command"), MAX_COMMAND)
            try:
                out["interval"] = max(MIN_INTERVAL, min(MAX_INTERVAL, int(w.get("interval", DEFAULT_INTERVAL))))
            except (TypeError, ValueError):
                out["interval"] = DEFAULT_INTERVAL
        elif t == "scripts":
            items = w.get("items")
            out["items"] = [x for x in (clean_script(i) for i in (items if isinstance(items, list) else [])) if x][:MAX_SCRIPTS]
        elif t == "web":
            out["url"] = normalize_url(w.get("url"))
            try:
                out["zoom"] = max(MIN_ZOOM, min(MAX_ZOOM, int(w.get("zoom", 100))))
            except (TypeError, ValueError):
                out["zoom"] = 100
            try:
                out["refresh"] = max(0, min(MAX_REFRESH, int(w.get("refresh", 0))))
            except (TypeError, ValueError):
                out["refresh"] = 0
        elif t == "links":
            items = w.get("items")
            out["items"] = [{"label": one_line(i.get("label"), 40), "target": one_line(i.get("target"), MAX_COMMAND)}
                            for i in (items if isinstance(items, list) else []) if isinstance(i, dict)
                            and one_line(i.get("label"), 40) and one_line(i.get("target"), MAX_COMMAND)][:MAX_LINKS]
        elif t == "image":
            out["path"] = one_line(w.get("path"), MAX_PATH)
        return out

    def get(self, wid):
        return next((dict(w) for w in self.items if w["id"] == wid), None)

    def add(self, wtype, title="", **fields):
        """A new widget; returns its id. Raises ValueError for an unknown type or when there are too many."""
        if wtype not in TYPES:
            raise ValueError(f"unknown kind of widget: {wtype}")
        if len(self.items) >= MAX_WIDGETS:
            raise ValueError(f"{MAX_WIDGETS} widgets is the most")
        used = {int(w["id"][1:]) for w in self.items}
        wid = f"w{next(n for n in range(1, MAX_WIDGETS + 2) if n not in used)}"
        w = self._clean(dict(fields, id=wid, type=wtype, title=title))
        self.items.append(w)
        return wid

    def update(self, wid, **fields):
        for i, w in enumerate(self.items):
            if w["id"] == wid:
                self.items[i] = self._clean(dict(w, **fields, id=wid, type=w["type"]))
                return self.items[i]
        raise ValueError("no such widget")

    def delete(self, wid):
        self.items = [w for w in self.items if w["id"] != wid]

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "widgets": self.items}, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)


def main(argv):
    ws = Widgets()
    for w in ws.items:
        print(f"{w['id']}  {TYPES[w['type']]:<16} {w['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
