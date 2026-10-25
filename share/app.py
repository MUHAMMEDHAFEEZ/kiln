#!/usr/bin/env python3
"""Kiln: interactive desktop cards, a real embedded terminal and desktop icons for GNOME.

GTK3 on X11/Xwayland (GNOME has no layer-shell, so the windows are borderless,
sticky, kept below normal windows and hidden from alt-tab / taskbar).
"""
import os
os.environ["GDK_BACKEND"] = "x11"

import ast
import datetime
import json
import math
import operator
import re
import signal
import shlex
import shutil
import subprocess
import sys
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Vte", "2.91")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GdkX11, Gio, GLib, Gtk, Pango, PangoCairo, Vte  # noqa: E402
import cairo  # noqa: E402

HOME = os.path.expanduser("~")
SHARE = os.path.dirname(os.path.abspath(__file__))           # the code (~/.local/share/kiln)
APP = os.environ.get("KILN_CONFIG", f"{HOME}/.config/kiln")  # the user's settings and data
CACHE = os.environ.get("KILN_CACHE", f"{HOME}/.cache/kiln")
ICONS = f"{SHARE}/icons/"
TODO = f"{APP}/todo.txt"
UPDATER = f"{SHARE}/update-data.sh"
sys.path.insert(0, SHARE)
import iconkit as K  # noqa: E402
import prayer as PR  # noqa: E402
import accounts as ACC  # noqa: E402
FONT = "Ubuntu Sans"

# KILN_DEMO=<file.json> shows made-up projects, servers and programs instead of this computer's (for
# screenshots and the README; see docs/CONTRIBUTING.md). Without it nothing here is used.
try:
    with open(os.environ["KILN_DEMO"], encoding="utf-8") as _f:
        DEMO = json.load(_f)
except (KeyError, OSError, ValueError):
    DEMO = None

# ---- settings (~/.config/kiln/config.json, documented in docs/CONFIGURATION.md)
DEFAULT_CONFIG = {
    "desktop_name": "{Host}'s Desktop",
    "clock": "12h",
    "desktop_dir": "",
    "project_dirs": ["~/Desktop"],
    "web_search": {"name": "DuckDuckGo", "url": "https://duckduckgo.com/?q={q}"},
    "integrations": {"github": True, "lark": False, "claude_code": True},
    "now_playing": {"youtube_music_only": False},
    "terminal": {"project_commands": True},
    "modes": {"focus_mutes_notifications": True},
    "icons": {"recent_dot": True, "recent_hours": 48},
    "dock": {"cards": ["git", "system", "day", "notes", "tests"], "position": "left"},
    "week_start": "sun",
    "performance": "auto",
    "updates": {"mode": "notify"},
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def _load_config():
    try:
        with open(f"{APP}/config.json", encoding="utf-8") as f:
            return _merge(DEFAULT_CONFIG, json.load(f))
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)


CFG = _load_config()


def _light_mode():
    """True on a weak computer ("performance": "auto" looks at cores and memory), or when asked for."""
    want = str(CFG.get("performance", "auto")).lower()
    if want in ("low", "light"):
        return True
    if want in ("normal", "high"):
        return False
    try:
        with open("/proc/meminfo") as f:
            mem_kb = int(next(l for l in f if l.startswith("MemTotal")).split()[1])
    except (OSError, StopIteration, ValueError):
        mem_kb = 8_000_000
    return (os.cpu_count() or 4) <= 2 or mem_kb < 4_500_000


LIGHT = _light_mode()
SLOW = 2.5 if LIGHT else 1.0          # how much slower the background timers run in light mode


def every(seconds):
    """A timer period in whole seconds, stretched in light mode."""
    return max(1, round(seconds * SLOW))


INTEGRATIONS = CFG.get("integrations", {})
H24 = str(CFG.get("clock", "12h")).lower().startswith("24")
HM = "%H:%M" if H24 else "%-I:%M"               # clock without the AM/PM part
HMP = "%H:%M" if H24 else "%-I:%M %p"           # clock as shown next to other text

C = dict(card="161222", text="f1edff", dim="8c85a8", acc="b794f6", blue="7aa8ff",
         cyan="7dcfff", teal="5eead4", amber="f2c98a", mint="73daca", violet="8b6cf0",
         red="f7768e", yellow="f2c98a", green="73daca")
PALETTE = ["5b8cff", "34d399", "a78bfa", "fb923c", "22d3ee", "f472b6"]

# ---- theme (see themes.json; switch with `kiln theme <name>`)
THEMES_FILE = f"{SHARE}/themes.json"
USER_THEMES_FILE = f"{APP}/themes.json"         # optional: extra / overridden themes
CURRENT_THEME_FILE = f"{APP}/current-theme"
C.update(card="0e1433", border="7384ff", ink="ffffff", acc2="c4b5fd", acc_hi="d6c4ff", on_acc="0e1433",
         chip="cdc6ec", sel="7c5cf0", on_sel="ffffff", ph1="3b2a7a", ph2="12204a", shadow="000000",
         menu="0e1433", menu_fg="f1edff")
TV = dict(mode="dark", card_a=0.70, border_a=0.38, sheen_a=0.06, shadow_a=0.55)


def load_themes():
    """Bundled themes plus the user's own (~/.config/kiln/themes.json), which win on a name clash."""
    with open(THEMES_FILE, encoding="utf-8") as f:
        data = json.load(f)
    try:
        with open(USER_THEMES_FILE, encoding="utf-8") as f:
            extra = json.load(f)
        data["themes"].update(extra.get("themes", {}))
        data["order"] += [n for n in extra.get("order", list(extra.get("themes", {}))) if n not in data["order"]]
        for k, v in extra.get("folder_palettes", {}).items():
            data["folder_palettes"][k] = v
    except (OSError, ValueError):
        pass
    return data


def _load_theme():
    try:
        data = load_themes()
        name = "kiln-dark"
        if os.path.exists(CURRENT_THEME_FILE):
            name = open(CURRENT_THEME_FILE, encoding="utf-8").read().strip() or name
        return name, data["themes"].get(name), data
    except (OSError, ValueError, KeyError):
        return "kiln-dark", None, None


THEME_NAME, THEME, THEMES_DATA = _load_theme()
if THEME:
    C.update(THEME.get("colors", {}))
    TV.update(THEME.get("values", {}))
    TV["mode"] = THEME.get("mode", "dark")


_SYS = (THEME or {}).get("system", {})
if _SYS.get("project_palette"):
    PALETTE[:] = list(_SYS["project_palette"])


def folder_svg_themed(color, glyph="none"):
    fn = K.folder_matte_svg if _SYS.get("folder_style") == "matte" else K.folder_svg
    return fn(color, glyph)


def tone(hexs):
    """Soften a vivid accent so it sits well in muted themes (no-op for Violet Dusk)."""
    k = TV.get("mute", 0.0 if THEME_NAME == "violet-dusk" else 0.35)
    if not k:
        return hexs
    a = [int(hexs[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(C["dim"][i:i + 2], 16) for i in (0, 2, 4)]
    return "%02x%02x%02x" % tuple(int(a[i] * (1 - k) + b[i] * k) for i in range(3))


def theme_names():
    if not THEMES_DATA:
        return []
    return [(n, THEMES_DATA["themes"][n].get("label", n)) for n in THEMES_DATA.get("order", THEMES_DATA["themes"])
            if n in THEMES_DATA["themes"]]


def switch_theme(name):
    """Apply a theme via the theme tool in its own unit (it restarts this app)."""
    run(["systemd-run", "--user", "--collect", "--quiet", "/usr/bin/python3", f"{SHARE}/theme.py", name])

# ----------------------------------------------------------------- helpers
def rgb(hexs):
    return tuple(int(hexs[i:i + 2], 16) / 255 for i in (0, 2, 4))


def set_rgba(cr, hexs, a=1.0):
    r, g, b = rgb(hexs)
    cr.set_source_rgba(r, g, b, a)


def rrect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def card_bg(cr, x, y, w, h, r=20, alpha=None):
    """Theme card: fill, optional faint top sheen, hairline/glow edge."""
    rrect(cr, x + 0.5, y + 0.5, w - 1, h - 1, r)
    set_rgba(cr, C["card"], max(alpha or 0, TV["card_a"]))
    cr.fill_preserve()
    if TV["sheen_a"] > 0:
        sheen = cairo.LinearGradient(0, y, 0, y + min(h, 150))
        sheen.add_color_stop_rgba(0, 1, 1, 1, TV["sheen_a"])
        sheen.add_color_stop_rgba(1, 1, 1, 1, 0.0)
        cr.set_source(sheen)
        cr.fill_preserve()
    cr.set_line_width(1.2)
    set_rgba(cr, C["border"], TV["border_a"])
    cr.stroke()


def text(cr, s, x, y, size, color, bold=False, align="l", maxw=None, alpha=1.0, strike=False, mono=False,
         ltr=False):
    """Draw text with its baseline at y. Returns the drawn width. ltr=True keeps Arabic text in a
    left-to-right paragraph, so alignment and `maxw` behave like for any other text."""
    lay = PangoCairo.create_layout(cr)
    if ltr:
        lay.set_auto_dir(False)
        lay.get_context().set_base_dir(Pango.Direction.LTR)
    fd = Pango.FontDescription()
    fd.set_family("Hack" if mono else FONT)
    fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    fd.set_absolute_size(size * Pango.SCALE)
    lay.set_font_description(fd)
    lay.set_text(str(s), -1)
    if maxw:
        lay.set_width(int(maxw * Pango.SCALE))
        lay.set_ellipsize(Pango.EllipsizeMode.END)
    w, _h = lay.get_pixel_size()
    base = lay.get_baseline() / Pango.SCALE
    px = x if align == "l" else (x - w if align == "r" else x - w / 2)
    set_rgba(cr, color, alpha)
    cr.move_to(px, y - base)
    PangoCairo.show_layout(cr, lay)
    if strike:
        cr.set_line_width(1.2)
        cr.move_to(px, y - size * 0.3)
        cr.line_to(px + w, y - size * 0.3)
        cr.stroke()
    cr.new_path()
    return w


_icons = {}


def _icon(name):
    s = _icons.get(name)
    if s is None:
        try:
            s = cairo.ImageSurface.create_from_png(ICONS + name + ".png")
        except Exception:
            s = False
        _icons[name] = s
    return s


def glyph(cr, name, x, y, size, color, alpha=1.0):
    """White glyph PNG tinted with a colour."""
    s = _icon(name)
    if not s:
        return
    cr.save()
    cr.translate(x, y)
    k = size / s.get_width()
    cr.scale(k, k)
    pat = cairo.SurfacePattern(s)
    pat.set_filter(cairo.FILTER_BEST)
    set_rgba(cr, color, alpha)
    cr.mask(pat)
    cr.restore()
    cr.new_path()


def picture(cr, name, x, y, size):
    s = _icon(name)
    if not s:
        return
    cr.save()
    cr.translate(x, y)
    k = size / s.get_width()
    cr.scale(k, k)
    pat = cairo.SurfacePattern(s)
    pat.set_filter(cairo.FILTER_BEST)
    cr.set_source(pat)
    cr.paint()
    cr.restore()
    cr.new_path()


def hline(cr, x1, x2, y, a=0.08):
    cr.set_line_width(1)
    set_rgba(cr, C["ink"], a)
    cr.move_to(x1, y + 0.5)
    cr.line_to(x2, y + 0.5)
    cr.stroke()


def vline(cr, x, y1, y2, a=0.08):
    cr.set_line_width(1)
    set_rgba(cr, C["ink"], a)
    cr.move_to(x + 0.5, y1)
    cr.line_to(x + 0.5, y2)
    cr.stroke()


def read_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [l.rstrip("\n") for l in f if l.strip()]
    except OSError:
        return []


def ago(ts):
    d = max(0, time.time() - float(ts))
    if d < 3600:
        return f"{max(1, int(d // 60))}m"
    if d < 86400:
        return f"{int(d // 3600)}h"
    if d < 86400 * 30:
        return f"{int(d // 86400)}d"
    return f"{int(d // (86400 * 30))}mo"


def clean_env():
    env = dict(os.environ)
    env.pop("GDK_BACKEND", None)
    return env


_procs = []


def run(cmd):
    """Fire-and-forget launcher that does not leak GDK_BACKEND=x11 to child apps."""
    try:
        _procs.append(subprocess.Popen(cmd, env=clean_env(), start_new_session=True,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    except OSError:
        pass


_dock_watch = []


def schedule_update_checks():
    """Look for a newer release a couple of minutes after start and then every few hours. The check runs as
    its own systemd unit, because installing an update restarts Kiln (and would stop a child of Kiln)."""
    if DEMO or str((CFG.get("updates") or {}).get("mode", "notify")).lower() == "off":
        return

    def go():
        cmd = ["/usr/bin/python3", f"{SHARE}/update.py", "auto"]
        if shutil.which("systemd-run"):
            env = [f"-E{k}={os.environ[k]}" for k in ("DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
                                                       "XDG_RUNTIME_DIR") if os.environ.get(k)]
            cmd = ["systemd-run", "--user", "--collect", "--quiet", "--unit=kiln-update", *env, *cmd]
        run(cmd)
        return True
    GLib.timeout_add_seconds(150, lambda: (go(), False)[1])
    GLib.timeout_add_seconds(6 * 3600, go)


def enforce_dock_position():
    """Keep the Ubuntu Dock where the layout expects it (config "dock.position", default left; "" = leave it)."""
    pos = str((CFG.get("dock") or {}).get("position", "left")).upper()
    src = Gio.SettingsSchemaSource.get_default()
    if DEMO or pos not in ("LEFT", "RIGHT", "BOTTOM", "TOP") or not src or \
            not src.lookup("org.gnome.shell.extensions.dash-to-dock", True):
        return
    st = Gio.Settings.new("org.gnome.shell.extensions.dash-to-dock")

    def fix(*_):
        if st.get_string("dock-position") != pos:
            st.set_string("dock-position", pos)
    fix()
    st.connect("changed::dock-position", fix)       # moved by hand? put it back
    _dock_watch.append(st)


def open_url(url):
    if str(url).startswith(("http://", "https://")):      # never hand xdg-open a file: or custom-scheme address
        run(["xdg-open", url])


def refresh(what):
    run([UPDATER, what])


def copy_text(s):
    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(s, -1)


def hash_color(name):
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) % 1000003
    return PALETTE[h % len(PALETTE)]


# ----------------------------------------------------------------- data
class Metrics:
    def __init__(self):
        self._cpu_prev = None
        self.cpu = 0.0
        self.gpu = 0.0
        self._gpu_t = 0.0
        self._gpu_dev = self._find_gpu()

    @staticmethod
    def _find_gpu():
        base = "/sys/bus/pci/drivers/nvidia"
        try:
            for n in os.listdir(base):
                if ":" in n:
                    return f"/sys/bus/pci/devices/{n}"
        except OSError:
            pass
        return None

    def sample(self):
        try:
            with open("/proc/stat") as f:
                v = [int(x) for x in f.readline().split()[1:8]]
            idle, total = v[3] + v[4], sum(v)
            if self._cpu_prev:
                di, dt = idle - self._cpu_prev[0], total - self._cpu_prev[1]
                self.cpu = 100.0 * (1 - di / dt) if dt > 0 else 0.0
            self._cpu_prev = (idle, total)
        except (OSError, ValueError, IndexError):
            pass
        # GPU: never wake a sleeping dGPU; query at most every 15 s
        if self._gpu_dev and time.time() - self._gpu_t > 15:
            self._gpu_t = time.time()
            try:
                st = open(self._gpu_dev + "/power/runtime_status").read().strip()
            except OSError:
                st = "active"
            if st == "suspended":
                self.gpu = 0.0
            else:
                try:
                    out = subprocess.run(
                        ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=3).stdout.strip().splitlines()
                    self.gpu = float(out[0]) if out else 0.0
                except (OSError, ValueError, subprocess.SubprocessError):
                    self.gpu = 0.0

    @staticmethod
    def mem():
        try:
            d = {}
            with open("/proc/meminfo") as f:
                for l in f:
                    k, v = l.split(":")
                    d[k] = int(v.split()[0])
            return 100.0 * (1 - d["MemAvailable"] / d["MemTotal"])
        except (OSError, KeyError, ValueError):
            return 0.0

    @staticmethod
    def battery():
        for b in ("BAT1", "BAT0"):
            try:
                return float(open(f"/sys/class/power_supply/{b}/capacity").read())
            except (OSError, ValueError):
                continue
        return 0.0


def load_names():
    m = {}
    for l in read_lines(f"{APP}/project-names.txt"):
        if l.lstrip().startswith("#"):
            continue
        p = [x.strip() for x in l.split("|")]
        if p and p[0]:
            col = p[2].lstrip("#") if len(p) > 2 and len(p[2].lstrip("#")) == 6 else None
            m[p[0]] = (p[1] if len(p) > 1 and p[1] else None, col)
    return m


def load_projects():
    rows = []
    for l in read_lines(f"{CACHE}/projects.txt"):
        p = l.split("|")
        if p[0] == "total" or len(p) < 5:
            continue
        rows.append(dict(ts=p[0], name=p[1], branch=p[2], dirty=int(p[3] or 0), path=p[4],
                         lang=p[5] if len(p) > 5 else ""))
    return rows


def load_github():
    d, prs = {}, []
    for l in read_lines(f"{CACHE}/github.txt"):
        p = l.split("|")
        if p[0] == "pr" and len(p) >= 5:
            prs.append(dict(repo=p[1], num=p[2], ts=p[3], title="|".join(p[4:])))
        elif len(p) >= 2:
            d[p[0]] = p[1]
    return d, prs


def todo_items(path=TODO):
    items = []
    for i, l in enumerate(read_lines_keep(path)):
        if l.startswith("[ ]"):
            items.append(dict(idx=i, text=l[3:].strip(), done=False))
        elif l.startswith("[x]"):
            items.append(dict(idx=i, text=l[3:].strip(), done=True))
    return items


def read_lines_keep(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [l.rstrip("\n") for l in f]
    except OSError:
        return []


def write_lines(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    os.replace(tmp, path)


def todo_toggle(idx, path=TODO):
    ls = read_lines_keep(path)
    if 0 <= idx < len(ls):
        if ls[idx].startswith("[ ]"):
            ls[idx] = "[x]" + ls[idx][3:]
        elif ls[idx].startswith("[x]"):
            ls[idx] = "[ ]" + ls[idx][3:]
        write_lines(path, ls)


def todo_delete(idx, path=TODO):
    ls = read_lines_keep(path)
    if 0 <= idx < len(ls):
        del ls[idx]
        write_lines(path, ls)


def todo_add(txt, path=TODO):
    txt = txt.strip()
    if txt:
        ls = read_lines_keep(path)
        ls.append("[ ] " + txt)
        write_lines(path, ls)


def todo_to_top(idx, path=TODO):
    ls = read_lines_keep(path)
    if 0 <= idx < len(ls):
        ls.insert(0, ls.pop(idx))
        write_lines(path, ls)


PTODO_DIR = f"{APP}/project-todos"


def ptodo_file(project_path):
    """The user's todo list for a project (the same file `kiln tasks` and the Claude Code hook use)."""
    return f"{PTODO_DIR}/{re.sub(r'[^A-Za-z0-9._-]', '_', os.path.basename(project_path))}.txt"


_claude_cache = {}
_stime_cache = {}


def session_time(fp):
    """Time of the last message in a Claude Code transcript, cached per file version."""
    try:
        mt = os.path.getmtime(fp)
    except OSError:
        return 0
    if (fp, mt) not in _stime_cache:
        t = 0
        try:
            with open(fp, "rb") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 65536))
                ts = re.findall(r'"timestamp":"([^"]+)"', fh.read().decode("utf-8", "replace"))
            if ts:
                t = datetime.datetime.fromisoformat(ts[-1].replace("Z", "+00:00")).timestamp()
        except (OSError, ValueError):
            pass
        _stime_cache[(fp, mt)] = t
    return _stime_cache[(fp, mt)]


def claude_tasks(project_path, max_age=7 * 86400):
    """Tasks (TaskCreate / TaskUpdate) from the newest Claude Code session in this project.
    Sessions live in ~/.claude/projects/<path with every non-alphanumeric as '-'>/<id>.jsonl;
    folders the project was moved from are matched by the project's name."""
    root = os.path.expanduser("~/.claude/projects")
    enc = re.sub(r"[^A-Za-z0-9]", "-", project_path)
    tail = "-" + re.sub(r"[^A-Za-z0-9]", "-", os.path.basename(project_path))
    try:
        dirs = [d for d in os.listdir(root) if d == enc or d.endswith(tail)]
    except OSError:
        return None
    files = []
    for d in dirs:
        try:
            for f in os.listdir(os.path.join(root, d)):
                if f.endswith(".jsonl"):
                    fp = os.path.join(root, d, f)
                    files.append((os.path.getmtime(fp), fp))
        except OSError:
            pass
    files = sorted(((session_time(fp), fp, mt) for mt, fp in files), reverse=True)
    for st, fp, mt in files[:20]:
        if time.time() - st > max_age:
            break
        key = (fp, mt)
        if key not in _claude_cache:
            _claude_cache[key] = _parse_tasks(fp)
        tasks = _claude_cache[key]
        if tasks:
            return dict(session=os.path.basename(fp)[:-6], mtime=st, tasks=tasks,
                        live=time.time() - st < 180)
    return None


def _parse_tasks(fp):
    tasks, creates = {}, {}
    try:
        f = open(fp, encoding="utf-8", errors="replace")
    except OSError:
        return []
    with f:
        for line in f:
            if "TaskCreate" not in line and "TaskUpdate" not in line and '"task"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            content = (d.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if c.get("type") == "tool_use" and c.get("name") == "TaskCreate":
                    creates[c.get("id")] = c.get("input") or {}
                elif c.get("type") == "tool_use" and c.get("name") == "TaskUpdate":
                    inp = c.get("input") or {}
                    t = tasks.get(str(inp.get("taskId")))
                    if t is not None:
                        if inp.get("status") == "deleted":
                            tasks.pop(t["id"], None)
                            continue
                        for k in ("status", "subject", "activeForm"):
                            if inp.get(k):
                                t[k] = inp[k]
                elif c.get("type") == "tool_result" and c.get("tool_use_id") in creates:
                    task = (d.get("toolUseResult") or {}).get("task") or {}
                    inp = creates.pop(c["tool_use_id"])
                    if task.get("id"):
                        tid = str(task["id"])
                        tasks[tid] = dict(id=tid, subject=task.get("subject") or inp.get("subject", ""),
                                          activeForm=inp.get("activeForm", ""), status="pending")
    return list(tasks.values())


def _py(path):
    """The project's own Python (venv / .venv / env) if it has one."""
    for d in ("venv", ".venv", "env"):
        exe = os.path.join(path, d, "bin", "python")
        if os.path.exists(exe):
            return shlex.quote(exe)
    return "python3"


def _json_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _make_targets(path):
    for n in ("Makefile", "makefile", "GNUmakefile"):
        try:
            with open(os.path.join(path, n), encoding="utf-8", errors="replace") as f:
                return re.findall(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)", f.read(), re.M)
        except OSError:
            continue
    return []


def _has_pytest(path):
    if any(os.path.exists(os.path.join(path, n)) for n in ("pytest.ini", "tox.ini", "conftest.py")):
        return True
    for n in ("pyproject.toml", "setup.cfg"):
        try:
            with open(os.path.join(path, n), encoding="utf-8", errors="replace") as f:
                if "pytest" in f.read():
                    return True
        except OSError:
            pass
    for d in ("tests", "test"):
        try:
            if any(f.endswith(".py") for f in os.listdir(os.path.join(path, d))):
                return True
        except OSError:
            pass
    return False


def _node_pm(path):
    for lock, pm in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun")):
        if os.path.exists(os.path.join(path, lock)):
            return pm
    return "npm"


_cmd_cache = {}
_STAMP_FILES = ("package.json", "Makefile", "pyproject.toml", "manage.py", "Cargo.toml", "go.mod", "pubspec.yaml",
                "docker-compose.yml", "docker-compose.yaml", "compose.yaml", "pom.xml", "pytest.ini")


def _stamp(path):
    out = []
    for n in _STAMP_FILES:
        try:
            out.append(os.path.getmtime(os.path.join(path, n)))
        except OSError:
            out.append(0)
    return tuple(out)


def project_commands(path):
    """(label, command) pairs worth a button for the project in `path`, read from its own files."""
    key = ("cmds", path)
    st = _stamp(path)
    if _cmd_cache.get(key, (None,))[0] == st:
        return _cmd_cache[key][1]
    out = []

    def add(label, cmd=None):
        cmd = cmd or label
        if all(c != cmd for _l, c in out):
            out.append((label, cmd))
    pkg = _json_file(os.path.join(path, "package.json"))
    if isinstance(pkg, dict) and isinstance(pkg.get("scripts"), dict):
        pm = _node_pm(path)
        for sc in ("dev", "start", "serve", "build", "test", "lint"):
            body = pkg["scripts"].get(sc)
            if isinstance(body, str) and "no test specified" not in body:
                add(f"{pm} run {sc}" if pm == "npm" else f"{pm} {sc}")
    if os.path.exists(os.path.join(path, "manage.py")):
        py = _py(path)
        for sub in ("runserver", "test", "migrate"):
            add(f"manage.py {sub}", f"{py} manage.py {sub}")
    elif _has_pytest(path):
        add("pytest", f"{_py(path)} -m pytest -q")
    targets = _make_targets(path)
    for t in ("run", "dev", "serve", "up", "build", "test", "lint"):
        if t in targets:
            add(f"make {t}")
    if any(os.path.exists(os.path.join(path, n)) for n in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml")):
        add("docker compose up")
    if os.path.exists(os.path.join(path, "Cargo.toml")):
        add("cargo run")
        add("cargo test")
    if os.path.exists(os.path.join(path, "go.mod")):
        add("go run .")
        add("go test ./...")
    if os.path.exists(os.path.join(path, "pubspec.yaml")):
        add("flutter run")
        add("flutter test")
    if os.path.exists(os.path.join(path, "pom.xml")):
        add("mvn test")
    if os.path.exists(os.path.join(path, "gradlew")):
        add("gradlew build", "./gradlew build")
    res = out[:6]
    _cmd_cache[key] = (st, res)
    return res


def test_command(path):
    """The command that runs this project's tests, or None."""
    if os.path.exists(os.path.join(path, "manage.py")):
        return f"{_py(path)} manage.py test"
    pkg = _json_file(os.path.join(path, "package.json"))
    if isinstance(pkg, dict) and isinstance(pkg.get("scripts"), dict):
        body = pkg["scripts"].get("test")
        if isinstance(body, str) and "no test specified" not in body:
            pm = _node_pm(path)
            return "npm test" if pm == "npm" else f"{pm} test"
    if _has_pytest(path):
        return f"{_py(path)} -m pytest -q"
    for f, cmd in (("Cargo.toml", "cargo test"), ("go.mod", "go test ./..."), ("pubspec.yaml", "flutter test"),
                   ("pom.xml", "mvn -q test")):
        if os.path.exists(os.path.join(path, f)):
            return cmd
    if "test" in _make_targets(path):
        return "make test"
    if os.path.exists(os.path.join(path, "gradlew")):
        return "./gradlew test"
    return None


WORK = dict(proj=None)      # the project the bottom bar is showing (set by WorkBar)


# ----------------------------------------------------------------- work data (GitHub json, connected accounts)
def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _ts(v):
    """Best-effort timestamp (seconds) from ms / s / ISO / {'timestamp': ..} values."""
    if v in (None, "", 0, "0"):
        return None
    if isinstance(v, dict):
        for k in ("timestamp", "date_time", "datetime", "time", "date"):
            if v.get(k) not in (None, ""):
                return _ts(v[k])
        return None
    try:
        n = float(v)
        return n / 1000 if n > 1e11 else n
    except (TypeError, ValueError):
        pass
    try:
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


ACCT_DONE_FILE = f"{CACHE}/accounts-done.json"


def accounts_data():
    """{service id: {ok, error, tasks, events}} as last synced (see accounts.py)."""
    return ACC.read_cache().get("services", {})


def accounts_state(kind):
    """('none', '') nothing connected for `kind`, ('pending', '') not synced yet, ('error', text), ('ok', '')"""
    ids = [i for i in ACC.connected() if kind in ACC.SERVICES[i].kinds]
    if not ids:
        return "none", ""
    data = accounts_data()
    have = [data[i] for i in ids if i in data]
    if not have:
        return "pending", ""
    if all(not d.get("ok") for d in have):
        return "error", next((d.get("error") for d in have if d.get("error")), "")
    return "ok", ""


def load_acct_done():
    d = load_json(ACCT_DONE_FILE) or {}
    if d.get("date") != time.strftime("%Y-%m-%d"):
        return {}
    return d.get("done", {})


def save_acct_done(done):
    try:
        ACC.write_private(ACCT_DONE_FILE, {"date": time.strftime("%Y-%m-%d"), "done": done})
    except OSError:
        pass


def load_account_tasks():
    out = []
    for sid, c in accounts_data().items():
        if sid not in ACC.SERVICES:
            continue
        for t in c.get("tasks") or []:
            out.append(dict(key=f"{sid}:{t['id']}", guid=t["id"], service=sid, text=t["title"], due=t.get("due"),
                            url=t.get("url", ""), detail=t.get("detail", ""), source="account", done=False))
    return out


def load_account_events():
    out = []
    for sid, c in accounts_data().items():
        if sid in ACC.SERVICES:
            out += [dict(e, service=sid) for e in c.get("events") or []]
    out.sort(key=lambda e: e["start"])
    return out


def load_gh():
    d = load_json(f"{CACHE}/github.json") or {}
    data = d.get("data") or {}
    v = data.get("viewer") or {}
    prs = []
    for n in (v.get("pullRequests") or {}).get("nodes") or []:
        try:
            ci = n["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["state"]
        except (KeyError, IndexError, TypeError):
            ci = None
        prs.append(dict(num=n.get("number"), title=n.get("title", ""), url=n.get("url", ""),
                        ts=_ts(n.get("updatedAt")), ci=ci, review=False))
    revs = []
    reviews = data.get("reviews") or {}
    for n in reviews.get("nodes") or []:
        if n:
            revs.append(dict(num=n.get("number"), title=n.get("title", ""), url=n.get("url", ""),
                             ts=_ts(n.get("updatedAt")), ci=None, review=True))
    weeks = (((v.get("contributionsCollection") or {}).get("contributionCalendar") or {}).get("weeks")) or []
    days = [[c.get("contributionCount", 0) for c in w.get("contributionDays", [])] for w in weeks]
    return dict(user=v.get("login"), notif=d.get("notif", 0), prs=prs, revs=revs,
                pr_count=(v.get("pullRequests") or {}).get("totalCount", len(prs)),
                rev_count=reviews.get("issueCount", len(revs)), weeks=days)


def due_label(ts, clock=True):
    """(text, colour) for a due timestamp."""
    lt = time.localtime(ts)
    d = datetime.date(lt.tm_year, lt.tm_mon, lt.tm_mday)
    today = datetime.date.today()
    hm = "" if not clock or (lt.tm_hour, lt.tm_min) in ((0, 0), (23, 59)) else time.strftime(" " + HMP, lt)
    if d < today:
        return "Overdue", C["red"]
    if d == today:
        return ("Today" + hm), (C["red"] if ts < time.time() else C["amber"])
    if d == today + datetime.timedelta(days=1):
        return "Tomorrow" + hm, C["dim"]
    return time.strftime("%b %d", lt), C["dim"]


# ----------------------------------------------------------------- layout
COL, GAP = 370, 18
X2 = COL + GAP
LAY = dict(strip=(0, 0, COL, 96), today=(0, 114, COL, 272), agenda=(0, 404, COL, 168),
           github=(X2, 0, COL, 318), projects=(X2, 336, COL, 236))
CARDS_W, CARDS_H = COL * 2 + GAP, 572
PROJ_ROWS, TODO_ROWS, AGENDA_ROWS = 4, 5, 3
LANG_COLORS = {"Python": "4b8bff", "TypeScript": "2f8cff", "JavaScript": "f2c94c", "Dart": "2bc8c0",
               "Go": "22d3ee", "Rust": "fb923c", "Java": "f472b6", "Kotlin": "a78bfa"}


def inside(px, py, box):
    x, y, w, h = box
    return x <= px < x + w and y <= py < y + h


def popup_menu(event, items):
    m = Gtk.Menu()
    for label, cb in items:
        if label is None:
            m.append(Gtk.SeparatorMenuItem())
            continue
        it = Gtk.MenuItem(label=label)
        it.connect("activate", lambda _w, cb=cb: cb())
        m.append(it)
    m.show_all()
    m.popup_at_pointer(event)


def pill(cr, x, y, label, size, color, bg_alpha=0.16, align="l", bold=False, fg=None):
    """Small rounded tag; (x, y) is the text baseline. Returns its width."""
    cr.save()
    lay = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription()
    fd.set_family(FONT)
    fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    fd.set_absolute_size(size * Pango.SCALE)
    lay.set_font_description(fd)
    lay.set_text(label, -1)
    tw, _th = lay.get_pixel_size()
    w = tw + 14
    bx = x - w if align == "r" else x
    rrect(cr, bx, y - size - 2, w, size + 8, (size + 8) / 2)
    set_rgba(cr, color, bg_alpha)
    cr.fill()
    cr.restore()
    text(cr, label, bx + 7, y, size, fg or color, bold=bold)
    return w


# ----------------------------------------------------------------- cards window
class Cards(Gtk.Overlay):
    def __init__(self, terminal):
        super().__init__()
        self.terminal = terminal
        self.metrics = Metrics()
        self.hist = {"cpu": [], "ram": []}
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(CARDS_W, CARDS_H)
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK |
                             Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.SCROLL_MASK |
                             Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.area.connect("draw", self.on_draw)
        self.area.connect("motion-notify-event", self.on_motion)
        self.area.connect("leave-notify-event", self.on_leave)
        self.area.connect("button-press-event", self.on_press)
        self.area.connect("scroll-event", self.on_scroll)
        self.add(self.area)

        tx, ty, tw, _th = LAY["today"]
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Add a task and press Enter…")
        self.entry.set_size_request(tw - 28, 36)
        self.entry.set_halign(Gtk.Align.START)
        self.entry.set_valign(Gtk.Align.START)
        self.entry.set_margin_start(tx + 14)
        self.entry.set_margin_top(ty + LAY["today"][3] - 46)
        self.entry.set_no_show_all(True)
        self.entry.get_style_context().add_class("kiln-entry")
        self.entry.connect("activate", self.on_entry_activate)
        self.entry.connect("key-press-event", self.on_entry_key)
        self.entry.connect("focus-out-event", lambda *_: self.entry.hide())
        self.add_overlay(self.entry)

        self.hits, self.hover = [], None
        self.proj_off = self.todo_off = 0
        self._n_proj = self._n_todo = 0
        self.items = {}
        self.acct_done = load_acct_done()
        self.today_tab = "today"           # "today" (personal + connected accounts) or "project" (WORK["proj"])
        self.jobs = []                     # (Popen, guid, text)

    # --- metrics history (called from the 2 s tick)
    def push_history(self):
        for k, v in (("cpu", self.metrics.cpu), ("ram", self.metrics.mem())):
            h = self.hist[k]
            h.append(v)
            del h[:-30]

    def poll_jobs(self):
        for job in list(self.jobs):
            p, guid, _t = job
            rc = p.poll()
            if rc is None:
                continue
            self.jobs.remove(job)
            if rc != 0:                        # completion failed: undo the optimistic tick
                self.acct_done.pop(guid, None)
                save_acct_done(self.acct_done)
            refresh("accounts")

    # --- hit regions
    def region(self, cr, box, key, r=10, hl=True):
        self.hits.append((box, key))
        if hl and self.hover == key:
            x, y, w, h = box
            rrect(cr, x, y, w, h, r)
            set_rgba(cr, C["ink"], 0.07)
            cr.fill()

    def hit_at(self, px, py):
        for box, key in reversed(self.hits):
            if inside(px, py, box):
                return key
        return None

    def on_motion(self, _w, ev):
        key = self.hit_at(ev.x, ev.y)
        if key != self.hover:
            self.hover = key
            win = self.area.get_window()
            if win:
                win.set_cursor(Gdk.Cursor.new_from_name(Gdk.Display.get_default(), "pointer") if key else None)
            self.area.queue_draw()

    def on_leave(self, *_):
        if self.hover is not None:
            self.hover = None
            self.area.queue_draw()

    # --- actions
    def complete_task(self, it):
        if it["key"] in self.acct_done:
            return
        self.acct_done[it["key"]] = it["text"]
        save_acct_done(self.acct_done)
        try:
            p = subprocess.Popen(["/usr/bin/python3", f"{SHARE}/accounts.py", "complete", it["service"], it["guid"]],
                                 env=clean_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.jobs.append((p, it["key"], it["text"]))
        except OSError:
            self.acct_done.pop(it["key"], None)

    def toggle_item(self, key):
        it = self.items.get(key)
        if not it:
            return
        if it["source"] == "account":
            if not it["done"]:
                self.complete_task(it)
        elif it["source"] == "project":
            todo_toggle(it["idx"], it["file"])
        elif it["source"] == "local":
            todo_toggle(it["idx"])

    def open_accounts(self):
        run(["/usr/bin/python3", f"{SHARE}/settings.py", "accounts"])

    def on_press(self, _w, ev):
        key = self.hit_at(ev.x, ev.y)
        if key is None:
            return False
        kind = key[0]
        if ev.button == 3:
            return self.context_menu(ev, key)
        if ev.button == 2 and kind == "proj":
            run(["nautilus", self.proj_by_key(key)["path"]])
            return True
        if ev.button != 1:
            return False
        if kind == "strip":
            show_prayer_popup(ev.x_root, ev.y_root)
        elif kind == "task_toggle":
            self.toggle_item(key[1])
        elif kind == "task_row":
            it = self.items.get(key[1])
            if it and it["source"] == "account" and it.get("url"):
                open_url(it["url"])
        elif kind == "add":
            self.show_entry()
        elif kind == "tab":
            self.today_tab, self.todo_off = key[1], 0
        elif kind == "acct_connect":
            self.open_accounts()
        elif kind == "proj_setup":
            run(["/usr/bin/python3", f"{SHARE}/settings.py", "projects"])
        elif kind == "event":
            if key[2]:
                open_url(key[2])
        elif kind == "viewall" or kind == "page":
            n = max(1, self._n_proj)
            self.proj_off = (key[1] * PROJ_ROWS) % n if kind == "page" else (self.proj_off + PROJ_ROWS) % n
        elif kind == "proj":
            run(["code", self.proj_by_key(key)["path"]])
        elif kind == "tile":
            open_url({"notif": "https://github.com/notifications", "pr": "https://github.com/pulls",
                      "rev": "https://github.com/pulls/review-requested"}[key[1]])
        elif kind == "gh_user":
            u = load_gh().get("user")
            if u:
                open_url(f"https://github.com/{u}")
        elif kind == "gh_logo":
            refresh("github")
            refresh("projects")
            refresh("accounts")
        elif kind == "pr":
            open_url(key[1])
        self.area.queue_draw()
        return True

    def proj_by_key(self, key):
        return load_projects()[key[1]]

    def context_menu(self, ev, key):
        kind = key[0]
        if kind == "proj":
            p = self.proj_by_key(key)
            path = p["path"]
            popup_menu(ev, [
                ("Open in VS Code", lambda: run(["code", path])),
                ("cd here in the terminal", lambda: self.terminal.cd(path)),
                ("Open in a new terminal tab", lambda: self.terminal.new_tab(path)),
                ("Open folder", lambda: run(["nautilus", path])),
                (None, None),
                ("Copy path", lambda: copy_text(path)),
            ])
            return True
        if kind in ("task_row", "task_toggle"):
            it = self.items.get(key[1])
            if not it:
                return False
            if it["source"] == "claude":
                proj = WORK["proj"]
                items = [("Copy", lambda: copy_text(it["subject"]))]
                if proj:
                    f = ptodo_file(proj["path"])
                    items.append(("Add to my project todos",
                                  lambda: (todo_add(it["subject"], f), self.area.queue_draw())))
                popup_menu(ev, items)
            elif it["source"] == "project":
                idx, f = it["idx"], it["file"]
                popup_menu(ev, [
                    ("Toggle done", lambda: (todo_toggle(idx, f), self.area.queue_draw())),
                    ("Move to top", lambda: (todo_to_top(idx, f), self.area.queue_draw())),
                    ("Copy", lambda: copy_text(it["text"])),
                    (None, None),
                    ("Delete", lambda: (todo_delete(idx, f), self.area.queue_draw())),
                ])
            elif it["source"] == "account":
                name = ACC.SERVICES[it["service"]].name
                items = [(f"Open in {name}", lambda: open_url(it["url"]))] if it.get("url") else []
                if not it["done"]:
                    items.append((f"Mark complete in {name}", lambda: (self.complete_task(it), self.area.queue_draw())))
                items += [(None, None), ("Copy title", lambda: copy_text(it["text"]))]
                popup_menu(ev, items)
            else:
                idx = it["idx"]
                popup_menu(ev, [
                    ("Toggle done", lambda: (todo_toggle(idx), self.area.queue_draw())),
                    ("Move to top", lambda: (todo_to_top(idx), self.area.queue_draw())),
                    (None, None),
                    ("Delete", lambda: (todo_delete(idx), self.area.queue_draw())),
                ])
            return True
        if kind == "pr":
            url = key[1]
            popup_menu(ev, [("Open pull request", lambda: open_url(url)), ("Copy link", lambda: copy_text(url))])
            return True
        if kind == "event" and key[2]:
            url = key[2]
            popup_menu(ev, [("Join meeting", lambda: open_url(url)), ("Copy link", lambda: copy_text(url))])
            return True
        return False

    def on_scroll(self, _w, ev):
        step = 0
        if ev.direction == Gdk.ScrollDirection.UP:
            step = -1
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            step = 1
        elif ev.direction == Gdk.ScrollDirection.SMOOTH:
            self._acc = getattr(self, "_acc", 0.0) + ev.delta_y
            if abs(self._acc) >= 1:
                step = 1 if self._acc > 0 else -1
                self._acc = 0.0
        if not step:
            return True
        if inside(ev.x, ev.y, LAY["projects"]):
            self.proj_off = max(0, min(self.proj_off + step, max(0, self._n_proj - PROJ_ROWS)))
        elif inside(ev.x, ev.y, LAY["today"]):
            self.todo_off = max(0, min(self.todo_off + step, max(0, self._n_todo - TODO_ROWS)))
        self.area.queue_draw()
        return True

    def show_entry(self):
        self.entry.set_text("")
        self.entry.show()
        self.get_toplevel().present()
        self.entry.grab_focus()

    def on_entry_activate(self, _e):
        proj = WORK["proj"]
        if self.today_tab == "project" and proj:
            todo_add(self.entry.get_text(), ptodo_file(proj["path"]))
        else:
            todo_add(self.entry.get_text())
        self.entry.hide()
        self.area.queue_draw()

    def on_entry_key(self, _e, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.entry.hide()
            return True
        return False

    # --- drawing
    def on_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        self.hits = []
        self.card_strip(cr)
        self.card_today(cr)
        self.card_agenda(cr)
        self.card_github(cr)
        self.card_projects(cr)
        return False

    def card(self, cr, name):
        x, y, w, h = LAY[name]
        card_bg(cr, x, y, w, h, 20)
        return x, y, w, h

    def header(self, cr, x, y, icon, color, title):
        glyph(cr, icon, x + 20, y + 16, 22, color)
        text(cr, title, x + 52, y + 34, 16, C["text"], bold=True)

    def scrollbar(self, cr, x, y, h, total, shown, off):
        if total <= shown:
            return
        th = max(18, h * shown / total)
        ty = y + (h - th) * (off / max(1, total - shown))
        rrect(cr, x, ty, 3, th, 1.5)
        set_rgba(cr, C["ink"], 0.22)
        cr.fill()

    def sparkline(self, cr, x, y, w, h, vals, color):
        if len(vals) < 2:
            return
        pts = [(x + w * i / (len(vals) - 1), y + h - h * max(0, min(100, v)) / 100) for i, v in enumerate(vals)]
        cr.new_path()
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.set_line_width(1.6)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        set_rgba(cr, color, 1)
        cr.stroke_preserve()
        cr.line_to(x + w, y + h)
        cr.line_to(x, y + h)
        cr.close_path()
        grad = cairo.LinearGradient(0, y, 0, y + h)
        r, g, b = rgb(color)
        grad.add_color_stop_rgba(0, r, g, b, 0.28)
        grad.add_color_stop_rgba(1, r, g, b, 0.0)
        cr.set_source(grad)
        cr.fill()

    # 1) clock + vitals strip
    def card_strip(self, cr):
        x, y, w, h = self.card(cr, "strip")
        self.region(cr, (x + 8, y + 8, w - 16, h - 16), ("strip",), r=12)
        tw = text(cr, time.strftime(HM), x + 20, y + 54, 40, C["text"])
        if not H24:
            text(cr, time.strftime("%p"), x + 26 + tw, y + 54, 14, C["dim"], bold=True)
        text(cr, time.strftime("%a, %b %d"), x + 22, y + 78, 13, C["dim"])
        vline(cr, x + 150, y + 18, y + h - 18)
        if "system" in DOCK_ACTIVE:                     # the System card already has CPU and RAM
            self.strip_dates(cr, x, y, w)
            return
        rows = [("CPU", self.metrics.cpu, self.hist["cpu"], C["acc"]),
                ("RAM", self.metrics.mem(), self.hist["ram"], C["acc2"])]
        for i, (label, val, hist, col) in enumerate(rows):
            cy = y + 36 + i * 32
            text(cr, label, x + 168, cy, 11, C["dim"])
            text(cr, f"{val:.0f}%", x + 238, cy, 14, C["text"], bold=True, align="r")
            self.sparkline(cr, x + 252, cy - 16, w - 252 - 20, 18, hist, col)

    def strip_dates(self, cr, x, y, w):
        """Hijri date, and the next prayer (or the week number when no city is set)."""
        today = datetime.date.today()
        arabic = (CFG.get("prayer") or {}).get("language", "en") == "ar"
        right = x + w - 20
        glyph(cr, "moon", x + 166, y + 22, 15, C["acc2"])
        text(cr, PR.hijri_text(today.year, today.month, today.day, arabic), x + 190, y + 36, 13, C["text"],
             maxw=right - x - 190, ltr=True)
        glyph(cr, "timer", x + 166, y + 54, 15, C["acc2"])
        state = prayer_state()
        if state:
            _t, name, when = state
            lang = (CFG.get("prayer") or {}).get("language", "en")
            nm = PRAYER_AR[name] if lang == "ar" else name
            nw = text(cr, nm, x + 190, y + 68, 13, C["acc"], bold=True, maxw=70, ltr=True)
            text(cr, when.strftime(HMP), x + 190 + nw + 8, y + 68, 13, C["acc"], bold=True)
            text(cr, in_words(when - datetime.datetime.now()), right, y + 68, 11, C["dim"], align="r")
        else:
            iso = today.isocalendar()
            text(cr, f"Week {iso[1]} · day {today.timetuple().tm_yday}", x + 190, y + 68, 13, C["text"],
                 maxw=right - x - 190)

    # 2) today: tasks of the connected accounts + local tasks
    def today_tabs(self, cr, x, y):
        """Header of the Today card: `Today` and, when known, the current project as a tab."""
        glyph(cr, "check-circle", x + 20, y + 16, 22, C["acc2"])
        proj = WORK["proj"]
        tabs = [("today", "Today")] + ([("project", proj["name"])] if proj else [])
        tx = x + 52
        for key, label in tabs:
            on = self.today_tab == key
            hot = self.hover == ("tab", key)
            tw = text(cr, label, tx, y + 34, 16 if on else 14, C["text"] if on or hot else C["dim"],
                      bold=on, maxw=130)
            self.hits.append(((tx - 6, y + 12, tw + 12, 30), ("tab", key)))
            tx += tw + 16

    def add_button(self, cr, x, y, w, h):
        ay = y + h - 22
        self.region(cr, (x + 14, ay - 21, 84, 30), ("add",), r=15, hl=False)
        hot = self.hover == ("add",)
        rrect(cr, x + 20, ay - 18, 72, 26, 13)
        set_rgba(cr, C["ink"], 0.10 if hot else 0.06)
        cr.fill_preserve()
        set_rgba(cr, C["border"], 0.9)
        cr.set_line_width(1)
        cr.stroke()
        cr.set_line_width(1.6)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        set_rgba(cr, C["acc"] if hot else C["text"], 1)
        cr.move_to(x + 34, ay - 10)
        cr.line_to(x + 34, ay)
        cr.move_to(x + 29, ay - 5)
        cr.line_to(x + 39, ay - 5)
        cr.stroke()
        text(cr, "Add", x + 46, ay, 13, C["acc"] if hot else C["text"])
        return ay

    def progress(self, cr, x, y, w, done, total):
        rrect(cr, x + 20, y + 50, w - 40, 4, 2)
        set_rgba(cr, C["ink"], 0.08)
        cr.fill()
        if total and done:
            rrect(cr, x + 20, y + 50, max(4, (w - 40) * done / total), 4, 2)
            set_rgba(cr, C["violet"], 1)
            cr.fill()

    def task_rows(self, cr, x, y, w, h, items):
        self.todo_off = max(0, min(self.todo_off, max(0, len(items) - TODO_ROWS)))
        for slot in range(TODO_ROWS):
            i = self.todo_off + slot
            if i >= len(items):
                break
            it = items[i]
            cy = y + 80 + slot * 32
            self.region(cr, (x + 10, cy - 15, w - 26, 30), ("task_row", it["key"]), r=10)
            claude = it["source"] == "claude"
            if not claude:
                self.hits.append(((x + 14, cy - 14, 34, 28), ("task_toggle", it["key"])))
            ring = C["acc2"] if claude else C["violet"]
            cr.new_path()
            if it["done"]:
                set_rgba(cr, ring, 0.75 if claude else 1)
                cr.arc(x + 30, cy, 9, 0, 2 * math.pi)
                cr.fill()
                glyph(cr, "check", x + 24.5, cy - 5.5, 11, C["on_acc"])
            elif it.get("running"):
                cr.set_line_width(1.8)
                set_rgba(cr, C["amber"], 1)
                cr.arc(x + 30, cy, 8.4, 0, 2 * math.pi)
                cr.stroke()
                cr.arc(x + 30, cy, 4, 0, 2 * math.pi)
                cr.fill()
            else:
                cr.set_line_width(1.8)
                set_rgba(cr, ring, 1)
                cr.arc(x + 30, cy, 8.4, 0, 2 * math.pi)
                cr.stroke()
            right = x + w - 22
            if it["source"] == "account":
                svc = ACC.SERVICES[it["service"]]
                right -= pill(cr, right, cy + 4, svc.name, 10.5, tone(svc.color), align="r") + 6
            elif claude:
                right -= pill(cr, right, cy + 4, "now" if it.get("running") else "Claude", 10.5,
                              C["amber"] if it.get("running") else C["acc2"], align="r") + 6
            if it.get("due") and not it["done"]:
                lbl, col = due_label(it["due"], clock=it["source"] != "account")   # the service pill needs the room
                right -= pill(cr, right, cy + 4, lbl, 10.5, col, align="r") + 6
            text(cr, it["text"], x + 50, cy + 5, 14, C["dim"] if it["done"] else C["text"],
                 maxw=right - (x + 50) - 4, strike=it["done"], alpha=0.8 if it["done"] else 1)
        self.scrollbar(cr, x + w - 8, y + 64, h - 76, len(items), TODO_ROWS, self.todo_off)

    def card_today(self, cr):
        if self.today_tab == "project":
            if WORK["proj"]:
                return self.card_project(cr)
            self.today_tab = "today"
        x, y, w, h = self.card(cr, "today")
        local = [dict(key=f"local:{i['idx']}", idx=i["idx"], text=i["text"], done=i["done"], due=None,
                      source="local", url="") for i in todo_items()]
        remote = load_account_tasks()
        open_keys = {t["key"] for t in remote}
        for t in remote:
            if t["key"] in self.acct_done:
                t["done"] = True
        remote_done_gone = [dict(key=k, guid=k.split(":", 1)[1], service=k.split(":", 1)[0], text=txt, done=True,
                                 due=None, source="account", url="")
                            for k, txt in self.acct_done.items() if k not in open_keys and k.split(":")[0] in ACC.SERVICES]
        allitems = remote + local + remote_done_gone
        opened = sorted([t for t in allitems if not t["done"]], key=lambda t: (t["due"] is None, t["due"] or 0))
        done = [t for t in allitems if t["done"]]
        items = opened + done
        self.items = {t["key"]: t for t in items}
        self._n_todo = len(items)

        self.today_tabs(cr, x, y)
        total = len(items)
        text(cr, f"{len(done)} of {total} done" if total else "no tasks", x + w - 22, y + 33, 12.5, C["dim"], align="r")
        ay = self.add_button(cr, x, y, w, h)
        counts = {}
        for t in items:
            if t["source"] == "account" and not t["done"]:
                counts[t["service"]] = counts.get(t["service"], 0) + 1
        if counts:
            text(cr, " · ".join(f"{n} {ACC.SERVICES[k].name}" for k, n in counts.items()), x + w - 22, ay, 11.5,
                 C["dim"], align="r", maxw=w - 140)
        elif not [i for i in ACC.connected() if "tasks" in ACC.SERVICES[i].kinds]:
            self.region(cr, (x + w - 190, ay - 18, 176, 24), ("acct_connect",), r=12)
            text(cr, "Connect your task apps", x + w - 22, ay, 11.5,
                 C["acc"] if self.hover == ("acct_connect",) else C["dim"], align="r")
        self.progress(cr, x, y, w, len(done), total)
        if not items:
            text(cr, "All clear.", x + 24, y + 104, 15, C["text"])
            text(cr, "Press Add to create a task", x + 24, y + 128, 12.5, C["dim"])
            return
        self.task_rows(cr, x, y, w, h, items)

    def card_project(self, cr):
        """The current project's list: the user's own todos (shared with Claude Code through
        `kiln tasks` and the SessionStart hook) plus the tasks of Claude's latest session there."""
        x, y, w, h = self.card(cr, "today")
        proj = WORK["proj"]
        f = ptodo_file(proj["path"])
        mine = [dict(key=f"p:{i['idx']}", idx=i["idx"], text=i["text"], done=i["done"], source="project",
                     file=f) for i in todo_items(f)]
        cl = proj.get("claude") or {}
        theirs = [dict(key=f"c:{t['id']}", subject=t["subject"], done=t["status"] == "completed",
                       running=t["status"] == "in_progress", source="claude",
                       text=t["activeForm"] if t["status"] == "in_progress" and t["activeForm"] else t["subject"])
                  for t in cl.get("tasks", [])]
        items = ([t for t in theirs if t["running"]] + [t for t in mine if not t["done"]] +
                 [t for t in theirs if not t["done"] and not t["running"]] +
                 [t for t in mine if t["done"]] + [t for t in theirs if t["done"]])
        self.items = {t["key"]: t for t in items}
        self._n_todo = len(items)
        done = len([t for t in items if t["done"]])

        self.today_tabs(cr, x, y)
        text(cr, f"{done} of {len(items)} done" if items else "no tasks", x + w - 22, y + 33, 12.5,
             C["dim"], align="r")
        ay = self.add_button(cr, x, y, w, h)
        if cl:
            label = "Claude · working now" if cl.get("live") else f"Claude · {since(cl['mtime'])} ago"
            lw = text(cr, label, x + w - 22, ay, 11.5, C["dim"], align="r")
            cr.new_path()
            cr.arc(x + w - 22 - lw - 9, ay - 4, 3.5, 0, 2 * math.pi)
            set_rgba(cr, C["mint"] if cl.get("live") else C["dim"], 1)
            cr.fill()
        self.progress(cr, x, y, w, done, len(items))
        if not items:
            text(cr, f"Nothing planned for {proj['name']}.", x + 24, y + 104, 15, C["text"], maxw=w - 48)
            text(cr, "Add a todo: Claude sees it next time", x + 24, y + 128, 12.5, C["dim"])
            text(cr, "it works in this project.", x + 24, y + 146, 12.5, C["dim"])
            return
        self.task_rows(cr, x, y, w, h, items)

    # 3) agenda (meetings from every connected calendar)
    def card_agenda(self, cr):
        x, y, w, h = self.card(cr, "agenda")
        self.header(cr, x, y, "calendar", C["blue"], "Agenda")
        st, err = accounts_state("events")
        if st != "ok":
            if st == "none":
                text(cr, "No calendar connected", x + 22, y + 76, 13.5, C["text"])
                text(cr, "Google, Outlook, Apple or Lark", x + 22, y + 98, 12.5, C["dim"])
            else:
                text(cr, "Loading meetings…" if st == "pending" else "Could not load meetings", x + 22, y + 76, 13.5,
                     C["text"])
                if err:
                    text(cr, err, x + 22, y + 98, 12, C["dim"], maxw=w - 44)
            self.region(cr, (x + 14, y + 112, w - 28, 40), ("acct_connect",), r=12)
            rrect(cr, x + 20, y + 118, w - 40, 30, 10)
            set_rgba(cr, C["acc"], 1 if self.hover == ("acct_connect",) else 0.9)
            cr.fill()
            text(cr, "Connect a calendar" if st == "none" else "Open account settings", x + w / 2, y + 138, 13,
                 C["on_acc"], bold=True, align="c")
            return
        now = time.time()
        night = time.mktime((datetime.date.today() + datetime.timedelta(days=1)).timetuple())
        events = [e for e in load_account_events() if e["end"] > now and e["start"] < night]
        text(cr, f"{len(events)} left today" if events else "", x + w - 22, y + 33, 12.5, C["dim"], align="r")
        if not events:
            text(cr, "No more meetings today", x + 22, y + 80, 13.5, C["dim"])
            return
        for slot, ev in enumerate(events[:AGENDA_ROWS]):
            cy = y + 72 + slot * 34
            live = ev["start"] <= now < ev["end"]
            self.region(cr, (x + 10, cy - 16, w - 20, 32), ("event", slot, ev["url"]), r=10)
            t = time.strftime(HM, time.localtime(ev["start"])) + "–" + time.strftime(HMP, time.localtime(ev["end"]))
            text(cr, t, x + 22, cy + 5, 12.5, C["acc"] if live else C["dim"])
            right = x + w - 22
            if ev["url"]:
                right -= pill(cr, right, cy + 4, "Join", 11, C["green"] if live else C["acc"], align="r",
                              bold=live) + 6
            if live:
                right -= pill(cr, right, cy + 4, "Now", 10.5, C["red"], align="r") + 6
            text(cr, ev["title"], x + 112, cy + 5, 14, C["text"], maxw=right - (x + 112) - 4)

    # 4) github
    def card_github(self, cr):
        x, y, w, h = self.card(cr, "github")
        gh = load_gh()
        self.region(cr, (x + 10, y + 8, 128, 36), ("gh_logo",), r=10)
        glyph(cr, "github", x + 20, y + 14, 24, C["text"])
        text(cr, "GitHub", x + 52, y + 34, 16, C["text"], bold=True)
        if not INTEGRATIONS.get("github", True):
            text(cr, "The GitHub integration is off.", x + 22, y + 90, 13, C["dim"])
            return
        if not shutil.which("gh"):
            text(cr, "Install the GitHub CLI (gh) and run", x + 22, y + 90, 13, C["dim"])
            text(cr, "gh auth login", x + 22, y + 112, 13, C["acc"], mono=True)
            return
        if gh["user"]:
            tw = text(cr, "@" + gh["user"], x + w - 22, y + 33, 12.5, C["dim"], align="r")
            self.region(cr, (x + w - 30 - tw, y + 14, tw + 16, 28), ("gh_user",), r=8)
        if not gh["user"]:
            text(cr, "connecting to github…", x + 22, y + 90, 13, C["dim"])
            return
        tiles = [("notif", gh["notif"], "Notifications"), ("pr", gh["pr_count"], "Your PRs"),
                 ("rev", gh["rev_count"], "Reviews")]
        tw_ = (w - 44) / 3
        for i, (key, val, label) in enumerate(tiles):
            tx = x + 22 + i * tw_
            self.region(cr, (tx - 8, y + 50, tw_ - 4, 52), ("tile", key), r=10)
            col = C["amber"] if key == "rev" and val else C["text"]
            text(cr, str(val), tx, y + 80, 22, col, bold=True)
            text(cr, label, tx, y + 96, 11.5, C["dim"])
        hline(cr, x + 20, x + w - 20, y + 110)

        rows = gh["revs"][:3] + gh["prs"]
        rows = rows[:3]
        if not rows:
            text(cr, "No open pull requests", x + 22, y + 140, 13, C["dim"])
        for i, pr in enumerate(rows):
            cy = y + 134 + i * 30
            self.region(cr, (x + 10, cy - 15, w - 20, 28), ("pr", pr["url"]), r=10)
            cx = x + 28
            cr.new_path()
            if pr["review"]:
                set_rgba(cr, C["amber"], 1)
                cr.arc(cx, cy, 5, 0, 2 * math.pi)
                cr.fill()
            elif pr["ci"] == "SUCCESS":
                set_rgba(cr, C["acc"] if TV.get("warm_ci") else C["green"], 1)
                cr.arc(cx, cy, 7.5, 0, 2 * math.pi)
                cr.fill()
                glyph(cr, "check", cx - 4.5, cy - 4.5, 9, C["on_acc"])
            elif pr["ci"] in ("FAILURE", "ERROR"):
                set_rgba(cr, C["red"], 1)
                cr.arc(cx, cy, 7.5, 0, 2 * math.pi)
                cr.fill()
                glyph(cr, "x", cx - 4.5, cy - 4.5, 9, C["on_acc"])
            elif pr["ci"] in ("PENDING", "EXPECTED"):
                cr.set_line_width(2)
                set_rgba(cr, C["amber"], 1)
                cr.arc(cx, cy, 6.5, -math.pi / 2, math.pi)
                cr.stroke()
            else:
                cr.set_line_width(1.5)
                set_rgba(cr, C["dim"], 0.8)
                cr.arc(cx, cy, 6, 0, 2 * math.pi)
                cr.stroke()
            nw = text(cr, f"#{pr['num']}", x + 44, cy + 5, 13, C["acc"], bold=True)
            right = x + w - 22
            right -= text(cr, ago(pr["ts"]) if pr["ts"] else "", right, cy + 5, 11.5, C["dim"], align="r") + 8
            if pr["review"]:
                right -= pill(cr, right, cy + 4, "review", 10.5, C["amber"], align="r") + 6
            text(cr, pr["title"], x + 44 + nw + 8, cy + 5, 13.5, C["text"], maxw=right - (x + 44 + nw + 8) - 4)

        if len(rows) < 3:
            n_pr, n_rev = gh["pr_count"], gh["rev_count"]
            summary = f"{n_pr} pull request{'s' if n_pr != 1 else ''}"
            if n_rev:
                summary += f"  ·  {n_rev} review{'s' if n_rev != 1 else ''} waiting"
            text(cr, summary, x + 22, y + 214, 12.5, C["dim"])
        # contributions heatmap (last 26 weeks)
        hline(cr, x + 20, x + w - 20, y + 228)
        weeks = gh["weeks"][-26:]
        flat = sorted(c for wk in weeks for c in wk if c)
        last = sum(c for wk in weeks for c in wk)
        text(cr, f"{last:,}", x + 22, y + 266, 22, C["text"], bold=True)
        text(cr, "contributions", x + 22, y + 284, 11.5, C["dim"])
        text(cr, "last 6 months", x + 22, y + 299, 11.5, C["dim"])
        cell, gap = 7, 2
        hx0 = x + w - 22 - len(weeks) * (cell + gap) + gap
        hy0 = y + 242
        self.region(cr, (hx0 - 6, hy0 - 6, len(weeks) * (cell + gap) + 10, 7 * (cell + gap) + 10), ("gh_user",), r=8)

        def level(c):
            if not c or not flat:
                return 0
            q = [flat[int(len(flat) * f)] for f in (0.25, 0.5, 0.75)]
            return 1 + sum(c > t for t in q)
        alphas = [0.07, 0.30, 0.50, 0.75, 1.0]
        for wi, wk in enumerate(weeks):
            for di, c in enumerate(wk):
                lv = level(c)
                rrect(cr, hx0 + wi * (cell + gap), hy0 + di * (cell + gap), cell, cell, 1.6)
                if lv == 0:
                    set_rgba(cr, C["ink"], alphas[0])
                else:
                    set_rgba(cr, C["acc"] if lv < 4 else C["acc_hi"], alphas[lv])
                cr.fill()

    # 5) projects
    def card_projects(self, cr):
        x, y, w, h = self.card(cr, "projects")
        self.header(cr, x, y, "folder", C["blue"], "Projects")
        rows = load_projects()
        self._n_proj = len(rows)
        pages = max(1, math.ceil(len(rows) / PROJ_ROWS))
        cur = min(pages - 1, self.proj_off // PROJ_ROWS)
        shown_pages = min(pages, 6)
        dx = x + w - 22 - (shown_pages - 1) * 12
        for i in range(shown_pages):
            self.region(cr, (dx + i * 12 - 6, y + 20, 12, 18), ("page", i), r=6, hl=False)
            cr.new_path()
            set_rgba(cr, C["acc"] if i == cur else C["ink"], 1 if i == cur else 0.22)
            cr.arc(dx + i * 12, y + 29, 3.2 if i == cur else 2.6, 0, 2 * math.pi)
            cr.fill()
        hline(cr, x + 20, x + w - 20, y + 48)
        if not rows:
            st = load_json(f"{CACHE}/projects.status") or {}
            dirs = ", ".join(d.replace(HOME, "~") for d in st.get("dirs") or []) or "your project folders"
            if st.get("state") == "done":
                text(cr, "No git projects found", x + 24, y + 80, 14, C["text"])
                text(cr, f"in {dirs}", x + 24, y + 102, 12, C["dim"], maxw=w - 48)
                self.region(cr, (x + 14, y + 118, w - 28, 36), ("proj_setup",), r=12)
                rrect(cr, x + 20, y + 124, w - 40, 26, 9)
                set_rgba(cr, C["acc"], 1 if self.hover == ("proj_setup",) else 0.9)
                cr.fill()
                text(cr, "Choose project folders", x + w / 2, y + 142, 12.5, C["on_acc"], bold=True, align="c")
            else:
                n = st.get("found") or 0
                total = st.get("total")
                text(cr, "Looking for projects…", x + 24, y + 80, 14, C["text"])
                text(cr, f"in {dirs}", x + 24, y + 102, 12, C["dim"], maxw=w - 48)
                if total:
                    text(cr, f"{total} folders to check · {n} ready", x + 24, y + 122, 12, C["dim"])
            return
        self.proj_off = max(0, min(self.proj_off, max(0, len(rows) - PROJ_ROWS)))
        names = load_names()
        used = set()
        for slot in range(PROJ_ROWS):
            i = self.proj_off + slot
            if i >= len(rows):
                break
            p = rows[i]
            cy = y + 76 + slot * 42
            self.region(cr, (x + 10, cy - 20, w - 20, 40), ("proj", i), r=12)
            sub, col = names.get(p["name"], (None, None))
            if _SYS.get("project_palette"):
                col = None
            if not col:
                start = PALETTE.index(hash_color(p["name"]))
                for k in range(len(PALETTE)):
                    cand = PALETTE[(start + k) % len(PALETTE)]
                    if cand not in used:
                        col = cand
                        break
            used.add(col)
            cr.new_path()
            set_rgba(cr, tone(col), 1)
            cr.arc(x + 28, cy, 6, 0, 2 * math.pi)
            cr.fill()
            right = x + w - 22
            right -= text(cr, ago(p["ts"]), right, cy + 5, 12, C["dim"], align="r") + 8
            if p["dirty"]:
                right -= pill(cr, right, cy + 4, f"±{p['dirty']}", 10.5, C["amber"], align="r") + 6
            if p.get("lang"):
                right -= (pill(cr, right, cy + 4, p["lang"], 10.5, C["acc"], bg_alpha=0.30, fg=C["acc_hi"], align="r")
                          if TV.get("badge_mono") else
                          pill(cr, right, cy + 4, p["lang"], 10.5, tone(LANG_COLORS.get(p["lang"], C["acc"])), align="r")) + 6
            maxw = right - (x + 46) - 6
            text(cr, p["name"], x + 46, cy - 1, 14.5, C["text"], maxw=maxw)
            text(cr, sub or p["branch"], x + 46, cy + 15, 11.5, C["dim"], maxw=maxw)


# ----------------------------------------------------------------- terminal (tabs + quick commands)
QUICK = f"{APP}/quick-commands.txt"


class TerminalCard(Gtk.Box):
    W, H, HEAD, FOOT = 880, 330, 44, 42
    PAL = ["#1a2044", "#f7768e", "#73daca", "#f2c98a", "#b794f6", "#d6a8ff", "#7dcfff", "#e4defa",
           "#4a5278", "#ff9eae", "#9ef0dc", "#f7dcae", "#cdb4fa", "#e6ccff", "#a6e0ff", "#f4f0ff"]
    DOTS = [("restart", "red", "Restart shell"), ("clear", "yellow", "Clear screen"),
            ("gnome", "green", "Open GNOME Terminal here")]

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.head = self._area(self.HEAD, self.on_head_draw, self.on_head_press, self.on_head_motion)
        self.stack = Gtk.Stack()
        self.foot = self._area(self.FOOT, self.on_foot_draw, self.on_foot_press, self.on_foot_motion)
        self.pack_start(self.head, False, False, 0)
        self.pack_start(self.stack, True, True, 0)
        self.pack_start(self.foot, False, False, 0)
        self.tabs, self.active = [], 0
        self.hud, self.on_hud, self.alpha = None, None, 0.80      # Focus mode: timer pill, solid card
        self.hover, self.hits, self.fhits = None, [], []
        self.branch = ""
        if not os.path.exists(QUICK):
            write_lines(QUICK, ["# one command per line (shown as buttons under the terminal)",
                                "git status", "git pull", "npm run dev", "docker ps", "code ."])
        self.new_tab(os.path.expanduser("~/Desktop"))
        GLib.timeout_add_seconds(every(1), self.poll_cwd)
        self._first_dir = not DEMO       # (a demo already starts in its project)

    def _area(self, h, draw, press, motion):
        a = Gtk.DrawingArea()
        a.set_size_request(self.W, h)
        a.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK |
                     Gdk.EventMask.LEAVE_NOTIFY_MASK)
        a.connect("draw", draw)
        a.connect("button-press-event", press)
        a.connect("motion-notify-event", motion)
        a.connect("leave-notify-event", lambda *_: self.set_hover(None))
        return a

    # --- tabs
    def make_term(self):
        t = Vte.Terminal()
        t.set_margin_start(20)
        t.set_margin_end(14)
        t.set_margin_top(4)
        t.set_font(Pango.FontDescription(f"Hack {getattr(self, 'font_pt', 11)}"))
        t.set_cell_height_scale(1.12)
        t.set_size(84, 9)
        t.set_scrollback_lines(5000)
        t.set_audible_bell(False)
        t.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
        t.set_cursor_shape(Vte.CursorShape.IBEAM)
        t.set_scroll_on_keystroke(True)
        pal = []
        for p in (THEME or {}).get("system", {}).get("term_palette", self.PAL):
            c = Gdk.RGBA()
            c.parse(p)
            pal.append(c)
        fg = Gdk.RGBA()
        fg.parse("#" + (THEME or {}).get("system", {}).get("term_fg", "e4defa"))
        t.set_colors(fg, Gdk.RGBA(0, 0, 0, 0), pal)
        t.set_color_background(Gdk.RGBA(0, 0, 0, 0))
        cur = Gdk.RGBA()
        cur.parse("#" + (THEME or {}).get("system", {}).get("term_cursor", "b794f6"))
        t.set_color_cursor(cur)
        cf = Gdk.RGBA()
        cf.parse("#" + C["on_acc"])
        t.set_color_cursor_foreground(cf)
        try:
            t.set_clear_background(False)
        except AttributeError:
            pass
        t.connect("key-press-event", self.on_key)
        t.connect("button-press-event", self.on_term_button)
        return t

    def set_font_pt(self, pt):
        self.font_pt = pt
        for tab in self.tabs:
            for pane in tab["panes"]:
                pane["term"].set_font(Pango.FontDescription(f"Hack {pt}"))

    def set_size(self, w, h):
        """Resize the card (the window is resized by the caller). The strips draw to self.W."""
        self.W, self.H = w, h
        self.head.set_size_request(300, self.HEAD)
        self.foot.set_size_request(300, self.FOOT)
        self.head.queue_draw()
        self.foot.queue_draw()
        self.queue_resize()

    def default_dir(self):
        """New tabs open in the project being worked on (the work bar knows it)."""
        proj = WORK.get("proj")
        if proj and os.path.isdir(proj["path"]):
            return proj["path"]
        return os.path.expanduser("~/Desktop")

    def new_tab(self, path=None):
        path = path or self.default_dir()
        t = self.make_term()
        pane = dict(term=t, pid=None)
        tab = dict(term=t, pid=None, cwd=path, start=path, root=t, panes=[pane], name=f"tab{id(t)}")
        self._hook(tab, pane)
        self.tabs.append(tab)
        self.stack.add_named(t, tab["name"])
        t.show_all()
        self.spawn(tab, pane)
        self.switch(len(self.tabs) - 1)
        return tab

    def new_tab_targets(self):
        """Menu items for a new tab anywhere: the project, the folder of this tab, the usual places, other
        recent projects, or a folder you pick."""
        items, seen = [], set()

        def add(label, path):
            if path and os.path.isdir(path) and path not in seen:
                seen.add(path)
                items.append((label, lambda p=path: self.new_tab(p)))
        proj = WORK.get("proj")
        if proj:
            add(f"Project: {proj['name']}", proj["path"])
        cwd = self.tabs[self.active]["cwd"]
        add(f"This folder: {os.path.basename(cwd.rstrip('/')) or '/'}", cwd)
        items.append((None, None))
        for name, rel in (("Home", ""), ("Desktop", "Desktop"), ("Documents", "Documents"), ("Downloads", "Downloads")):
            add(name, os.path.join(HOME, rel) if rel else HOME)
        recent = [r for r in load_projects() if r["path"] not in seen][:6]
        if recent:
            items.append((None, None))
            for r in recent:
                add(r["name"], r["path"])
        items.append((None, None))
        items.append(("Choose a folder…", self.pick_folder))
        return items

    def pick_folder(self):
        dlg = Gtk.FileChooserNative(title="Open a terminal in", action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.set_current_folder(self.tabs[self.active]["cwd"])
        if dlg.run() == Gtk.ResponseType.ACCEPT and dlg.get_filename():
            self.new_tab(dlg.get_filename())

    def _hook(self, tab, pane):
        t = pane["term"]
        t.connect("child-exited", lambda _t, _st: GLib.timeout_add(200, self.on_exit, tab, pane))
        t.connect("focus-in-event", lambda *_: self._focused(tab, pane))

    @staticmethod
    def _focused(tab, pane):
        tab["term"], tab["pid"] = pane["term"], pane["pid"]
        return False

    def spawn(self, tab, pane=None, start=None):
        pane = pane or tab["panes"][0]
        env = [f"{k}={v}" for k, v in clean_env().items()] + ["COLORTERM=truecolor"]

        def done(_t, pid, err, *_a):
            pane["pid"] = pid if not err else None
            if tab["term"] is pane["term"]:
                tab["pid"] = pane["pid"]
        pane["term"].spawn_async(Vte.PtyFlags.DEFAULT, start or tab["start"],
                                 ["/bin/bash", "--rcfile", f"{CACHE}/term.bashrc"], env,
                                 GLib.SpawnFlags.DEFAULT, None, None, -1, None, done)

    def on_exit(self, tab, pane=None):
        pane = pane or (tab["panes"][0] if tab["panes"] else None)
        if tab not in self.tabs or pane not in tab["panes"]:
            return False
        if len(tab["panes"]) > 1:
            self.close_pane(tab, pane, kill=False)
        elif len(self.tabs) == 1:
            pane["term"].reset(True, True)
            tab["start"] = tab["cwd"] if os.path.isdir(tab["cwd"]) else self.default_dir()
            self.spawn(tab, pane)
        else:
            self.close_tab(self.tabs.index(tab), kill=False)
        return False

    def close_tab(self, i, kill=True):
        if not (0 <= i < len(self.tabs)):
            return
        tab = self.tabs.pop(i)
        if kill:
            for pane in tab["panes"]:
                if pane["pid"]:
                    try:
                        os.kill(pane["pid"], signal.SIGHUP)
                    except OSError:
                        pass
        self.stack.remove(tab["root"])
        if not self.tabs:
            self.new_tab()
            return
        if i < self.active:
            self.active -= 1
        elif i == self.active:
            self.active = max(0, i - 1)
        self.switch(min(self.active, len(self.tabs) - 1))

    # --- panes: a tab can be split side by side or top / bottom
    def split(self, side_by_side=True):
        tab = self.tabs[self.active]
        old, cwd = tab["term"], tab["cwd"]
        t = self.make_term()
        pane = dict(term=t, pid=None)
        tab["panes"].append(pane)
        self._hook(tab, pane)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL if side_by_side else Gtk.Orientation.VERTICAL)
        parent = old.get_parent()
        if isinstance(parent, Gtk.Paned):
            first = parent.get_child1() is old
            parent.remove(old)
            (parent.pack1 if first else parent.pack2)(paned, True, False)
        else:
            self.stack.remove(old)
            self.stack.add_named(paned, tab["name"])
            tab["root"] = paned
        paned.pack1(old, True, False)
        paned.pack2(t, True, False)
        state = {"done": False}

        def half(p, alloc):
            if not state["done"] and alloc.width > 20 and alloc.height > 20:
                state["done"] = True
                size = alloc.width if p.get_orientation() == Gtk.Orientation.HORIZONTAL else alloc.height
                GLib.idle_add(lambda: (p.set_position(size // 2), False)[-1])
        paned.connect("size-allocate", half)
        paned.show_all()
        self.stack.set_visible_child(tab["root"])
        self.spawn(tab, pane, cwd)
        self._focused(tab, pane)
        t.grab_focus()

    def close_focused(self):
        tab = self.tabs[self.active]
        if len(tab["panes"]) > 1:
            pane = next(p for p in tab["panes"] if p["term"] is tab["term"])
            self.close_pane(tab, pane)
        else:
            self.close_tab(self.active)

    def close_pane(self, tab, pane, kill=True):
        t = pane["term"]
        parent = t.get_parent()
        if not isinstance(parent, Gtk.Paned):
            return
        tab["panes"].remove(pane)
        if kill and pane["pid"]:
            try:
                os.kill(pane["pid"], signal.SIGHUP)
            except OSError:
                pass
        sibling = parent.get_child2() if parent.get_child1() is t else parent.get_child1()
        grand = parent.get_parent()
        parent.remove(t)
        parent.remove(sibling)
        if isinstance(grand, Gtk.Paned):
            first = grand.get_child1() is parent
            grand.remove(parent)
            (grand.pack1 if first else grand.pack2)(sibling, True, False)
        else:
            self.stack.remove(parent)
            self.stack.add_named(sibling, tab["name"])
            tab["root"] = sibling
        sibling.show_all()
        if tab["term"] is t and tab["panes"]:
            self._focused(tab, tab["panes"][-1])
        self.stack.set_visible_child(tab["root"])
        tab["term"].grab_focus()

    def switch(self, i):
        self.active = i
        tab = self.tabs[i]
        self.stack.set_visible_child(tab["root"])
        tab["term"].grab_focus()
        self._update_branch()
        self.head.queue_draw()
        self.foot.queue_draw()

    @property
    def term(self):
        return self.tabs[self.active]["term"]

    def feed(self, s):
        try:
            self.term.feed_child(s.encode())
        except TypeError:
            self.term.feed_child(s, -1)
        self.term.grab_focus()

    def cd(self, path):
        self.feed(" cd " + shlex.quote(path) + "\n")

    def run_command(self, cmd):
        self.feed(cmd + "\n")

    def poll_cwd(self):
        proj = WORK.get("proj")
        sig = proj["path"] if proj else None
        if sig != getattr(self, "_chip_proj", None):         # the buttons follow the project
            self._chip_proj = sig
            self.foot.queue_draw()
        if getattr(self, "_first_dir", False) and proj and os.path.isdir(proj["path"]):
            self._first_dir = False
            tab = self.tabs[0]
            if len(self.tabs) == 1 and tab["cwd"] == os.path.expanduser("~/Desktop") and tab["pid"]:
                self.cd(proj["path"])
        changed = False
        for tab in self.tabs:
            if not tab["pid"]:
                continue
            try:
                p = os.readlink(f"/proc/{tab['pid']}/cwd")
            except OSError:
                continue
            if p != tab["cwd"]:
                tab["cwd"] = p
                changed = True
        if changed:
            self._update_branch()
            self.head.queue_draw()
            self.foot.queue_draw()
        return True

    def _update_branch(self):
        try:
            out = subprocess.run(["git", "-C", self.tabs[self.active]["cwd"], "rev-parse", "--abbrev-ref", "HEAD"],
                                 capture_output=True, text=True, timeout=1)
            self.branch = out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            self.branch = ""

    @staticmethod
    def short(p):
        return "~" + p[len(HOME):] if p.startswith(HOME) else p

    # --- keys / mouse in the terminal
    def on_key(self, _w, ev):
        mods = ev.state & Gtk.accelerator_get_default_mod_mask()
        cs = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        kv = ev.keyval
        if mods == cs and kv in (Gdk.KEY_C, Gdk.KEY_c):
            self.term.copy_clipboard_format(Vte.Format.TEXT)
        elif mods == cs and kv in (Gdk.KEY_V, Gdk.KEY_v):
            self.term.paste_clipboard()
        elif mods == cs and kv in (Gdk.KEY_T, Gdk.KEY_t):
            self.new_tab(self.tabs[self.active]["cwd"])
        elif mods == cs and kv in (Gdk.KEY_W, Gdk.KEY_w):
            self.close_focused()
        elif mods == cs and kv in (Gdk.KEY_E, Gdk.KEY_e):
            self.split(True)
        elif mods == cs and kv in (Gdk.KEY_O, Gdk.KEY_o):
            self.split(False)
        elif mods == Gdk.ModifierType.CONTROL_MASK and kv == Gdk.KEY_Page_Down:
            self.switch((self.active + 1) % len(self.tabs))
        elif mods == Gdk.ModifierType.CONTROL_MASK and kv == Gdk.KEY_Page_Up:
            self.switch((self.active - 1) % len(self.tabs))
        else:
            return False
        return True

    def on_term_button(self, _w, ev):
        if ev.button == 3:
            popup_menu(ev, [
                ("Copy", lambda: self.term.copy_clipboard_format(Vte.Format.TEXT)),
                ("Paste", lambda: self.term.paste_clipboard()),
                (None, None),
                ("New tab", lambda: self.new_tab(self.tabs[self.active]["cwd"])),
                ("Split right  (Ctrl+Shift+E)", lambda: self.split(True)),
                ("Split down  (Ctrl+Shift+O)", lambda: self.split(False)),
                ("Close pane / tab  (Ctrl+Shift+W)", self.close_focused),
                ("Clear", self.clear),
            ])
            return True
        return False

    def clear(self):
        self.term.reset(True, True)
        self.feed("\n")

    # --- header: tabs + window dots
    def set_hover(self, v):
        if v != self.hover:
            self.hover = v
            self.head.queue_draw()
            self.foot.queue_draw()

    def _hit(self, hits, px, py):
        for (x, y, w, h), key in reversed(hits):
            if x <= px < x + w and y <= py < y + h:
                return key
        return None

    def on_head_motion(self, _w, ev):
        self.set_hover(self._hit(self.hits, ev.x, ev.y))

    def on_head_press(self, _w, ev):
        key = self._hit(self.hits, ev.x, ev.y)
        if key is None:
            return False
        kind = key[0]
        if kind == "tab" and ev.button == 1:
            self.switch(key[1])
        elif kind == "tab" and ev.button == 2:
            self.close_tab(key[1])
        elif kind == "close":
            self.close_tab(key[1])
        elif kind == "plus" and ev.button == 1:
            self.new_tab(self.tabs[self.active]["cwd"])
        elif kind == "plusmenu" or (kind == "plus" and ev.button == 3):
            popup_menu(ev, self.new_tab_targets())
        elif kind == "hud" and self.on_hud:
            self.on_hud()
            self.head.queue_draw()
        elif kind == "dot":
            act = self.DOTS[key[1]][0]
            if act == "restart":
                tab = self.tabs[self.active]
                tab["term"].reset(True, True)
                if tab["pid"]:
                    try:
                        os.kill(tab["pid"], signal.SIGTERM)
                    except OSError:
                        pass
            elif act == "clear":
                self.clear()
            else:
                run(["gnome-terminal", "--working-directory=" + self.tabs[self.active]["cwd"]])
        return True

    def on_head_draw(self, _w, cr):
        self.hits = []
        rrect(cr, 16, 9, 32, 26, 8)
        set_rgba(cr, C["acc"], 0.16)
        cr.fill_preserve()
        set_rgba(cr, C["acc"], 0.45)
        cr.set_line_width(1)
        cr.stroke()
        cr.set_line_width(1.8)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        set_rgba(cr, C["text"], 1)
        cr.move_to(24, 16)
        cr.line_to(29, 22)
        cr.line_to(24, 28)
        cr.move_to(32, 28)
        cr.line_to(40, 28)
        cr.stroke()
        tx = 60
        for i, tab in enumerate(self.tabs):
            label = os.path.basename(tab["cwd"].rstrip("/")) or "/"
            if tab["cwd"] == HOME:
                label = "~"
            cr.save()
            lay = PangoCairo.create_layout(cr)
            fd = Pango.FontDescription()
            fd.set_family(FONT)
            fd.set_absolute_size(13 * Pango.SCALE)
            lay.set_font_description(fd)
            lay.set_text(label, -1)
            lw = min(150, lay.get_pixel_size()[0])
            cr.restore()
            closable = len(self.tabs) > 1
            w = lw + 26 + (18 if closable else 0)
            active = i == self.active
            hot = self.hover in (("tab", i), ("close", i))
            if active or hot:
                rrect(cr, tx, 8, w, 28, 9)
                set_rgba(cr, C["ink"], 0.09 if active else 0.05)
                cr.fill()
            if active:
                rrect(cr, tx + 10, 34, w - 20, 2, 1)
                set_rgba(cr, C["acc"], 1)
                cr.fill()
            text(cr, label, tx + 13, 27, 13, C["text"] if active else C["dim"], maxw=150)
            self.hits.append(((tx, 6, w, 32), ("tab", i)))
            if closable:
                cx = tx + w - 16
                self.hits.append(((cx - 7, 13, 16, 18), ("close", i)))
                glyph(cr, "x", cx - 5, 16.5, 11, C["text"] if self.hover == ("close", i) else C["dim"],
                      alpha=1 if (active or hot) else 0.6)
            tx += w + 4
        # "+" new tab
        self.hits.append(((tx, 8, 28, 28), ("plus",)))
        if self.hover == ("plus",):
            rrect(cr, tx, 8, 28, 28, 9)
            set_rgba(cr, C["ink"], 0.07)
            cr.fill()
        glyph(cr, "plus", tx + 7, 15, 14, C["dim"] if self.hover != ("plus",) else C["text"])
        # "▾": open a tab somewhere else (home, desktop, another project, any folder)
        mx = tx + 28
        self.hits.append(((mx, 8, 18, 28), ("plusmenu",)))
        if self.hover == ("plusmenu",):
            rrect(cr, mx, 8, 18, 28, 9)
            set_rgba(cr, C["ink"], 0.07)
            cr.fill()
        glyph(cr, "chevron-down", mx + 2, 16, 14, C["dim"] if self.hover != ("plusmenu",) else C["text"])
        # focus timer pill (only in Focus mode)
        if self.hud:
            label, running = self.hud()
            pw = pill(cr, self.W - 30 - len(self.DOTS) * 22 - 14, 28, label, 12.5, C["mint"] if running else C["acc"],
                      align="r", bold=True)
            self.hits.append(((self.W - 30 - len(self.DOTS) * 22 - 14 - pw, 10, pw, 26), ("hud",)))
        # dots
        for i, (_a, col, label) in enumerate(self.DOTS):
            dx = self.W - 30 - (len(self.DOTS) - 1 - i) * 22
            self.hits.append(((dx - 9, 13, 18, 18), ("dot", i)))
            hot = self.hover == ("dot", i)
            set_rgba(cr, C[col], 1 if hot else 0.85)
            cr.new_path()
            cr.arc(dx, 22, 6 + (1.5 if hot else 0), 0, 2 * math.pi)
            cr.fill()
        if self.hover and self.hover[0] == "dot":
            text(cr, self.DOTS[self.hover[1]][2], self.W - 96, 27, 12, C["dim"], align="r")
        hline(cr, 16, self.W - 16, self.HEAD - 2)
        return False

    # --- footer: quick commands + git branch
    def chips(self):
        """(label, command) buttons under the terminal: git, the project's own commands, then yours."""
        user = [l.strip() for l in read_lines(QUICK) if l.strip() and not l.lstrip().startswith("#")]
        proj = WORK.get("proj")
        cwd = self.tabs[self.active]["cwd"]
        if not (CFG.get("terminal") or {}).get("project_commands", True):
            return [(c, c) for c in user][:8]
        root = self._repo_of(cwd)                       # the buttons follow the folder this tab is in
        if root:
            out = [("git status", "git status"), ("git pull", "git pull")]
            out += [(label, cmd) for label, cmd in project_commands(root)]
            out += [(c, c) for c in user if all(c != x[0] for x in out)]
            return out[:12]
        out = [("ls -la", "ls -la")] + [(c, c) for c in user if c != "ls -la"]
        if proj:                                         # outside any repository: one tap back to the project
            out.insert(0, (f"↩ {proj['name']}", f"cd {shlex.quote(proj['path'])}"))
        return out[:10]

    _repo_cache = {}

    def _repo_of(self, cwd):
        """Git folder that contains `cwd`, or None (looked up once per folder)."""
        if cwd not in self._repo_cache:
            top = _git(cwd, "rev-parse", "--show-toplevel", timeout=2)
            self._repo_cache[cwd] = top if top and top not in (HOME, "/") else None
        return self._repo_cache[cwd]

    def on_foot_motion(self, _w, ev):
        self.set_hover(self._hit(self.fhits, ev.x, ev.y))

    def on_foot_press(self, _w, ev):
        key = self._hit(self.fhits, ev.x, ev.y)
        if key and key[0] == "chip" and ev.button == 1:
            self.run_command(key[1])
            return True
        if ev.button == 3:
            popup_menu(ev, [("Edit quick commands…", lambda: run(["xdg-open", QUICK]))])
            return True
        return False

    def on_foot_draw(self, _w, cr):
        self.fhits = []
        x = 18
        for label, cmd in self.chips():
            cr.save()
            lay = PangoCairo.create_layout(cr)
            fd = Pango.FontDescription("Hack")
            fd.set_absolute_size(11.5 * Pango.SCALE)
            lay.set_font_description(fd)
            lay.set_text(label, -1)
            lw = lay.get_pixel_size()[0]
            cr.restore()
            w = lw + 22
            if x + w > self.W - 170:
                break
            hot = self.hover == ("chip", cmd)
            rrect(cr, x, 8, w, 26, 13)
            set_rgba(cr, C["ink"], 0.09 if hot else 0.04)
            cr.fill_preserve()
            set_rgba(cr, C["acc"], 0.6 if hot else 0.3)
            cr.set_line_width(1)
            cr.stroke()
            set_rgba(cr, C["text"] if hot else C["chip"], 1)
            cr.move_to(x + 11, 8 + (26 - lay.get_pixel_size()[1]) / 2)
            PangoCairo.show_layout(cr, lay)
            self.fhits.append(((x, 6, w, 30), ("chip", cmd)))
            x += w + 8
        if self.branch:
            bw = text(cr, self.branch, self.W - 22, 26, 12, C["acc"], align="r", maxw=130)
            glyph(cr, "git-branch", self.W - 22 - bw - 20, 14, 14, C["acc"])
        return False


# ----------------------------------------------------------------- focus timer
FOCUS_FILE = f"{CACHE}/focus.json"


class FocusCard(Gtk.DrawingArea):
    W, H = 330, 330
    MODES = {"focus": 25 * 60, "break": 5 * 60}

    def __init__(self):
        super().__init__()
        self.set_size_request(self.W, self.H)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK |
                        Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.SCROLL_MASK)
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_press)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("leave-notify-event", lambda *_: self._hover(None))
        self.connect("scroll-event", self.on_scroll)
        self.mode = "focus"
        self.focus_len = self.MODES["focus"]
        self.length = self.focus_len
        self.left = float(self.length)
        self.running, self.end_at = False, 0.0
        st = load_json(FOCUS_FILE) or {}
        self.sessions = st.get("sessions", 0) if st.get("date") == time.strftime("%Y-%m-%d") else 0
        self.hover, self.hits = None, []
        GLib.timeout_add(500, self._tick)

    def _save(self):
        try:
            with open(FOCUS_FILE, "w", encoding="utf-8") as f:
                # running/end_at/left let the lock screen show a live countdown
                json.dump({"date": time.strftime("%Y-%m-%d"), "sessions": self.sessions,
                           "mode": self.mode, "running": self.running, "end_at": self.end_at,
                           "left": self.left, "length": self.length}, f)
        except OSError:
            pass

    def _tick(self):
        if self.running:
            self.left = max(0.0, self.end_at - time.time())
            if self.left <= 0:
                self.finish()
            self.queue_draw()
        return True

    def toggle(self):
        if self.running:
            self.left = max(0.0, self.end_at - time.time())
            self.running = False
        else:
            self.end_at = time.time() + self.left
            self.running = True
        self._save()
        self.queue_draw()

    def reset(self):
        self.running = False
        self.left = float(self.length)
        self._save()
        self.queue_draw()

    def set_mode(self, mode):
        self.mode = mode
        self.length = self.focus_len if mode == "focus" else self.MODES["break"]
        self.reset()

    def finish(self):
        self.running = False
        sound = "/usr/share/sounds/freedesktop/stereo/complete.oga"
        if os.path.exists(sound):
            run(["paplay", sound])
        if self.mode == "focus":
            self.sessions += 1
            self._save()
            run(["notify-send", "-a", "Kiln", "-i", "alarm-symbolic",
                 "Focus session done", "Nice work — take a 5 minute break."])
            self.set_mode("break")
        else:
            run(["notify-send", "-a", "Kiln", "-i", "alarm-symbolic",
                 "Break is over", "Ready for the next focus session?"])
            self.set_mode("focus")

    # input
    def _hit(self, px, py):
        for (x, y, w, h), key in reversed(self.hits):
            if x <= px < x + w and y <= py < y + h:
                return key
        return None

    def _hover(self, v):
        if v != self.hover:
            self.hover = v
            win = self.get_window()
            if win:
                win.set_cursor(Gdk.Cursor.new_from_name(Gdk.Display.get_default(), "pointer") if v else None)
            self.queue_draw()

    def on_motion(self, _w, ev):
        self._hover(self._hit(ev.x, ev.y))

    def on_press(self, _w, ev):
        key = self._hit(ev.x, ev.y)
        if ev.button != 1 or key is None:
            return False
        if key in ("play", "ring"):
            self.toggle()
        elif key == "reset":
            self.reset()
        elif key == "mode":
            self.set_mode("break" if self.mode == "focus" else "focus")
        return True

    def on_scroll(self, _w, ev):
        if self.running or self.mode != "focus":
            return True
        step = 5 * 60 if ev.direction == Gdk.ScrollDirection.UP else -5 * 60 \
            if ev.direction == Gdk.ScrollDirection.DOWN else 0
        if step:
            self.focus_len = self.length = max(5 * 60, min(90 * 60, self.length + step))
            self.left = float(self.length)
            self.queue_draw()
        return True

    # drawing
    def on_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        self.hits = []
        W, H = self.W, self.H
        card_bg(cr, 0, 0, W, H, 20)
        glyph(cr, "timer", 20, 16, 22, C["acc2"])
        text(cr, "Focus", 52, 34, 16, C["text"], bold=True)
        col = C["acc"] if self.mode == "focus" else C["mint"]
        mw = pill(cr, W - 20, 31, f"{self.focus_len // 60} min" if self.mode == "focus" else "Break", 11, col, align="r")
        self.hits.append(((W - 26 - mw, 12, mw + 12, 28), ("mode")))
        cx, cy, r = W / 2, 136, 64
        self.hits.append(((cx - r - 10, cy - r - 10, 2 * r + 20, 2 * r + 20), "ring"))
        cr.set_line_width(8)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.new_path()
        set_rgba(cr, C["ink"], 0.08)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        frac = self.left / self.length if self.length else 0
        if frac > 0.002:
            cr.new_path()
            set_rgba(cr, col, 1)
            cr.arc(cx, cy, r, -math.pi / 2, -math.pi / 2 + 2 * math.pi * frac)
            cr.stroke()
        secs = int(math.ceil(self.left))
        text(cr, f"{secs // 60:02d}:{secs % 60:02d}", cx, cy + 11, 32, C["text"], bold=True, align="c")
        state = "running" if self.running else ("paused" if self.left < self.length else "ready")
        text(cr, state, cx, cy + 31, 11.5, C["dim"], align="c")
        # buttons
        by = 244
        self.hits.append(((cx - 62, by - 16, 32, 32), "reset"))
        hot = self.hover == "reset"
        cr.new_path()
        set_rgba(cr, C["ink"], 0.10 if hot else 0.06)
        cr.arc(cx - 46, by, 16, 0, 2 * math.pi)
        cr.fill()
        glyph(cr, "restart", cx - 54, by - 8, 16, C["text"] if hot else C["dim"])
        self.hits.append(((cx - 22, by - 22, 44, 44), "play"))
        cr.new_path()
        set_rgba(cr, col, 1 if self.hover == "play" else 0.9)
        cr.arc(cx, by, 22, 0, 2 * math.pi)
        cr.fill()
        set_rgba(cr, C["on_acc"], 1)
        if self.running:
            cr.rectangle(cx - 7, by - 8, 5, 16)
            cr.rectangle(cx + 2, by - 8, 5, 16)
            cr.fill()
        else:
            cr.move_to(cx - 5, by - 9)
            cr.line_to(cx + 9, by)
            cr.line_to(cx - 5, by + 9)
            cr.close_path()
            cr.fill()
        # sessions
        n = self.sessions
        text(cr, f"{n} session{'s' if n != 1 else ''} today", cx, 310, 12, C["dim"], align="c")
        dots = min(8, max(4, n))
        for i in range(dots):
            cr.new_path()
            set_rgba(cr, C["acc"] if i < n else C["ink"], 1 if i < n else 0.15)
            cr.arc(cx - (dots - 1) * 6 + i * 12, 288, 3, 0, 2 * math.pi)
            cr.fill()
        return False


# ----------------------------------------------------------------- desktop icons
DESKTOP = os.path.expanduser(CFG.get("desktop_dir") or "") or \
    GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP) or f"{HOME}/Desktop"
ICON_ORDER = f"{APP}/icons.txt"
STYLES = f"{APP}/icon-styles.json"
CELL_W, CELL_H, COLS, ROWS = 88, 108, 11, 4
ICON_PX = 62
ICONS_W, ICONS_H = COLS * CELL_W, ROWS * CELL_H
ICONS_X, ICONS_Y = 890, 40
IMAGE_EXT = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff", "svg"}
ACCENT = C["sel"]

SEED_STYLES = {                      # first run only; `kiln theme` colours the folders after that
    "@home": {"color": "#2f8cff", "glyph": "home"},
}


def load_styles():
    try:
        import json
        with open(STYLES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_styles(d):
    import json
    os.makedirs(os.path.dirname(STYLES), exist_ok=True)
    tmp = STYLES + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STYLES)


def pixbuf_surface(pb):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, pb.get_width(), pb.get_height())
    c = cairo.Context(s)
    Gdk.cairo_set_source_pixbuf(c, pb, 0, 0)
    c.paint()
    return s


def thumb_surface(path):
    """macOS-style photo preview: white frame + soft shadow."""
    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 100, 100, True)
    S = 128
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, S, S)
    c = cairo.Context(s)
    w, h = pb.get_width(), pb.get_height()
    x, y = (S - w) / 2, (S - h) / 2 - 4
    for i, a in enumerate((0.06, 0.08, 0.10, 0.14)):
        g = 6 - i * 1.5
        rrect(c, x - 4 - g + 1, y - 4 - g + 6, w + 8 + 2 * g - 2, h + 8 + 2 * g - 2, 8 + g)
        c.set_source_rgba(0, 0, 0, a)
        c.fill()
    rrect(c, x - 4, y - 4, w + 8, h + 8, 6)
    c.set_source_rgba(1, 1, 1, 1)
    c.fill()
    c.save()
    rrect(c, x, y, w, h, 3)
    c.clip()
    Gdk.cairo_set_source_pixbuf(c, pb, x, y)
    c.paint()
    c.restore()
    return s


def ask_name(title, current):
    d = Gtk.Dialog(title=title)
    d.set_keep_above(True)
    d.set_position(Gtk.WindowPosition.CENTER)
    d.set_default_size(360, -1)
    entry = Gtk.Entry()
    entry.set_text(current)
    entry.set_activates_default(True)
    for m in ("start", "end", "top"):
        getattr(entry, f"set_margin_{m}")(12)
    entry.set_margin_bottom(6)
    d.get_content_area().add(entry)
    d.add_button("Cancel", Gtk.ResponseType.CANCEL)
    d.add_button("OK", Gtk.ResponseType.OK)
    d.set_default_response(Gtk.ResponseType.OK)
    d.show_all()
    base = current.rsplit(".", 1)[0] if "." in current.lstrip(".") else current
    entry.select_region(0, len(base))
    resp = d.run()
    name = entry.get_text().strip()
    d.destroy()
    return name if resp == Gtk.ResponseType.OK and name and "/" not in name else None


def pick_color(initial):
    d = Gtk.ColorChooserDialog(title="Folder color")
    d.set_keep_above(True)
    c = Gdk.RGBA()
    c.parse(initial)
    d.set_rgba(c)
    d.set_use_alpha(False)
    resp = d.run()
    rgba = d.get_rgba()
    d.destroy()
    if resp != Gtk.ResponseType.OK:
        return None
    return "#%02x%02x%02x" % (int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255))


class IconsGrid(Gtk.DrawingArea):
    TARGET = Gtk.TargetEntry.new("text/uri-list", 0, 0)

    def __init__(self, terminal):
        super().__init__()
        self.terminal = terminal
        self.set_size_request(ICONS_W, ICONS_H)
        self.set_can_focus(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK |
                        Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_press)
        self.connect("button-release-event", self.on_release)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("leave-notify-event", self.on_leave)
        self.connect("key-press-event", self.on_key)
        # drag & drop: this widget is both a source (files out) and a destination (files in / reorder)
        self.drag_dest_set(Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP, [self.TARGET],
                           Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        self.connect("drag-motion", self.on_drag_motion)
        self.connect("drag-leave", lambda *_: self._set_drop(None))
        self.connect("drag-data-received", self.on_drag_received)
        self.connect("drag-begin", self.on_drag_begin)
        self.connect("drag-data-get", self.on_drag_get)
        self.connect("drag-end", self.on_drag_end)

        self.items, self.slots, self.surfaces = {}, [], {}
        self.styles = load_styles()
        if self.styles is None:
            self.styles = dict(SEED_STYLES)
            save_styles(self.styles)
        self.sel, self.anchor, self.hover = set(), None, None
        self.press, self.dnd, self.rubber = None, False, None
        self.drag_keys, self.drop_slot = [], None
        self.pending = {}
        self._mt = self._mtimes()
        self.reload()
        self._mon = Gio.File.new_for_path(DESKTOP).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self._mon.connect("changed", lambda *_: GLib.timeout_add(250, self._reload_once))
        GLib.timeout_add_seconds(every(2), self._poll_files)

    # ---------------------------------------------------------- data
    @staticmethod
    def _mtimes():
        out = []
        for f in (ICON_ORDER, STYLES):
            try:
                out.append(os.stat(f).st_mtime)
            except OSError:
                out.append(0)
        return out

    def _poll_files(self):
        m = self._mtimes()
        if m != self._mt:
            self._mt = m
            self.styles = load_styles() or self.styles
            self.surfaces.clear()
            self.reload()
        return True

    def _reload_once(self):
        self.reload()
        return False

    def reload(self):
        items = {"@home": dict(key="@home", label="Home", path=HOME, kind="home", ext="")}
        try:
            names = sorted(os.listdir(DESKTOP), key=str.lower)
        except OSError:
            names = []
        for n in names:
            if n.startswith("."):
                continue
            path = os.path.join(DESKTOP, n)
            ext = n.rsplit(".", 1)[1].lower() if "." in n.lstrip(".") else ""
            kind = "app" if ext == "desktop" else ("dir" if os.path.isdir(path) else "file")
            label, appinfo = n, None
            if kind == "app":
                appinfo = Gio.DesktopAppInfo.new_from_filename(path)
                if appinfo is None:
                    continue
                label = appinfo.get_name()
            try:
                mt = os.stat(path).st_mtime
            except OSError:
                mt = 0
            items[n] = dict(key=n, label=label, path=path, kind=kind, ext=ext, app=appinfo, mtime=mt)
        self.items = items
        order = [l.strip() for l in read_lines_keep(ICON_ORDER) if l.strip() and not l.lstrip().startswith("#")]
        slots, seen = [], set()
        for k in order:
            if k == "-":
                slots.append(None)
            elif k in items and k not in seen:
                slots.append(k)
                seen.add(k)
        fresh = [k for k in items if k not in seen]
        slots += fresh
        for name, want in list(self.pending.items()):      # files dropped from other apps land where they were dropped
            if name in slots:
                slots.remove(name)
                self._place(slots, name, want)
                del self.pending[name]
                self.slots = slots
                self.save_order()
        self.slots = slots[:COLS * ROWS]
        self.sel &= set(items)
        self.surfaces = {k: v for k, v in self.surfaces.items() if k in items}
        self.queue_draw()

    @staticmethod
    def _place(slots, key, want):
        want = max(0, min(want, COLS * ROWS - 1))
        while len(slots) <= want:
            slots.append(None)
        if slots[want] is None:
            slots[want] = key
            return want
        for d in range(1, COLS * ROWS):
            for t in (want + d, want - d):
                if 0 <= t < COLS * ROWS:
                    while len(slots) <= t:
                        slots.append(None)
                    if slots[t] is None:
                        slots[t] = key
                        return t
        slots.append(key)
        return len(slots) - 1

    def save_order(self):
        head = [l for l in read_lines_keep(ICON_ORDER) if l.lstrip().startswith("#")]
        body = ["-" if k is None else k for k in self.slots]
        while body and body[-1] == "-":
            body.pop()
        write_lines(ICON_ORDER, head + body)
        self._mt = self._mtimes()

    # ---------------------------------------------------------- icon surfaces
    def surface(self, it):
        st = self.styles.get(it["key"], {})
        sig = (st.get("color"), st.get("glyph"), it.get("mtime"))
        cached = self.surfaces.get(it["key"])
        if cached and cached[0] == sig:
            return cached[1]
        s = None
        try:
            if it["kind"] in ("dir", "home"):
                s = pixbuf_surface(K.render(folder_svg_themed(st.get("color", K.DEFAULT_COLOR), st.get("glyph", "none")), 128))
            elif it["kind"] == "app" and it.get("app"):
                theme = Gtk.IconTheme.get_default()
                ii = theme.lookup_by_gicon(it["app"].get_icon(), 96, Gtk.IconLookupFlags.FORCE_SIZE)
                s = pixbuf_surface((ii or theme.lookup_icon("application-x-executable", 96, 0)).load_icon())
            elif it["ext"] in IMAGE_EXT:
                s = thumb_surface(it["path"])
            else:
                s = pixbuf_surface(K.render(K.doc_svg(it["ext"] or "file"), 128))
        except Exception:
            try:
                s = pixbuf_surface(K.render(K.doc_svg(it["ext"] or "file"), 128))
            except Exception:
                s = None
        self.surfaces[it["key"]] = (sig, s)
        return s

    # ---------------------------------------------------------- geometry
    @staticmethod
    def cell_xy(i):
        return (i % COLS) * CELL_W, (i // COLS) * CELL_H

    @staticmethod
    def slot_at(px, py):
        if px < 0 or py < 0 or px >= ICONS_W or py >= ICONS_H:
            return None
        return int(py // CELL_H) * COLS + int(px // CELL_W)

    def item_at(self, px, py):
        i = self.slot_at(px, py)
        if i is None or i >= len(self.slots) or self.slots[i] is None:
            return None
        return self.slots[i]

    def ordered_sel(self):
        return [k for k in self.slots if k and k in self.sel]

    # ---------------------------------------------------------- recent items + grouping
    def _repos(self):
        out = []
        for line in read_lines(f"{CACHE}/projects.all"):
            parts = line.split("|")
            if len(parts) >= 5 and parts[0].isdigit():
                out.append((int(parts[0]), parts[4]))
        return out

    def recent(self):
        """Keys of the items worth a dot: changed lately, or holding a repo with a recent commit."""
        now = time.time()
        if now - getattr(self, "_rec_t", 0) > 60:
            self._rec_t = now
            conf = CFG.get("icons") or {}
            rec = set()
            if conf.get("recent_dot", True):
                cutoff = now - float(conf.get("recent_hours", 48)) * 3600
                repos = self._repos()
                if not repos:
                    self._rec_t = now - 55          # the project list is still being written: try again soon
                for k, it in self.items.items():
                    if it["kind"] == "home":
                        continue
                    ts = it["mtime"]
                    if it["kind"] == "dir":
                        ts = max([ts] + [t for t, rp in repos if rp == it["path"] or rp.startswith(it["path"] + "/")])
                    if ts > cutoff:
                        rec.add(k)
            self._rec = rec
        return self._rec

    def group_icons(self, rows=True):
        """Project folders, other folders, then files and apps; each group starts a new row
        (or is only set off by a gap when rows would not fit). The order is saved like a drag."""
        keys = [k for k in self.slots if k]
        repos = [rp for _t, rp in self._repos()]

        def is_project(k):
            it = self.items[k]
            return it["kind"] == "dir" and (it["label"].lower().endswith("projects") or
                                            any(rp == it["path"] or rp.startswith(it["path"] + "/") for rp in repos))
        first = [k for k in keys if is_project(k)]
        second = [k for k in keys if k not in first and self.items[k]["kind"] in ("dir", "home")]
        rest = [k for k in keys if k not in first and k not in second]
        slots = []
        for grp in (first, second, rest):
            if not grp:
                continue
            if slots:
                if rows:
                    while len(slots) % COLS:
                        slots.append(None)
                else:
                    slots.append(None)
            slots += grp
        if len(slots) > COLS * ROWS:
            return self.group_icons(rows=False) if rows else False
        self.slots = slots
        self.save_order()
        self.queue_draw()
        return True

    def compact_icons(self):
        self.slots = [k for k in self.slots if k]
        self.save_order()
        self.queue_draw()

    # ---------------------------------------------------------- drawing
    def on_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        for i, key in enumerate(self.slots):
            if key and key in self.items:
                dim = 0.35 if (self.drag_keys and key in self.drag_keys) else 1.0
                self.draw_cell(cr, i, self.items[key], dim)
        if self.drop_slot is not None:
            x, y = self.cell_xy(self.drop_slot)
            rrect(cr, x + 5, y + 2, CELL_W - 10, CELL_H - 8, 14)
            set_rgba(cr, ACCENT, 0.9)
            cr.set_line_width(1.8)
            cr.set_dash([5, 4])
            cr.stroke()
            cr.set_dash([])
        if self.rubber:
            x0, y0, x1, y1 = self.rubber[:4]
            rrect(cr, min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), 4)
            set_rgba(cr, ACCENT, 0.16)
            cr.fill_preserve()
            set_rgba(cr, ACCENT, 0.8)
            cr.set_line_width(1)
            cr.stroke()
        return False

    def draw_icon(self, cr, it, x, y, size=ICON_PX, alpha=1.0):
        s = self.surface(it)
        if not s:
            return
        cr.save()
        cr.translate(x, y)
        k = size / s.get_width()
        cr.scale(k, k)
        pat = cairo.SurfacePattern(s)
        pat.set_filter(cairo.FILTER_BEST)
        cr.set_source(pat)
        cr.paint_with_alpha(alpha)
        cr.restore()
        cr.new_path()

    def draw_cell(self, cr, i, it, dim):
        x, y = self.cell_xy(i)
        sel, hot = it["key"] in self.sel, self.hover == it["key"]
        if sel or hot:
            rrect(cr, x + 9, y + 2, CELL_W - 18, ICON_PX + 14, 14)
            set_rgba(cr, C["ink"], 0.14 if sel else 0.06)
            cr.fill()
        self.draw_icon(cr, it, x + (CELL_W - ICON_PX) / 2, y + 8, alpha=dim)
        if it["key"] in self.recent():                    # touched in the last couple of days
            cx, cy = x + (CELL_W + ICON_PX) / 2 - 1, y + 13
            cr.new_path()
            cr.arc(cx, cy, 6.2, 0, 2 * math.pi)
            set_rgba(cr, C["card"], 0.95 * dim)
            cr.fill()
            cr.arc(cx, cy, 4.2, 0, 2 * math.pi)
            set_rgba(cr, C["acc"], dim)
            cr.fill()
        lay = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family(FONT)
        fd.set_absolute_size(12.5 * Pango.SCALE)
        lay.set_font_description(fd)
        lay.set_width((CELL_W - 10) * Pango.SCALE)
        lay.set_height(-2)
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay.set_ellipsize(Pango.EllipsizeMode.END)
        lay.set_alignment(Pango.Alignment.CENTER)
        lay.set_text(it["label"], -1)
        lx, ly = x + 5, y + ICON_PX + 15
        if sel:
            lo, hi = 10 ** 6, -10 ** 6
            it_ = lay.get_iter()
            while True:
                _ink, logic = it_.get_line_extents()
                lo = min(lo, logic.x / Pango.SCALE)
                hi = max(hi, (logic.x + logic.width) / Pango.SCALE)
                if not it_.next_line():
                    break
            _w, lh = lay.get_pixel_size()
            rrect(cr, lx + lo - 5, ly - 1, hi - lo + 10, lh + 2, 8)
            set_rgba(cr, ACCENT, 0.95)
            cr.fill()
        else:
            set_rgba(cr, C["shadow"], TV["shadow_a"] * dim)
            cr.move_to(lx, ly + 1)
            PangoCairo.show_layout(cr, lay)
        set_rgba(cr, C["on_sel"] if sel else C["text"], dim)
        cr.move_to(lx, ly)
        PangoCairo.show_layout(cr, lay)

    # ---------------------------------------------------------- selection + mouse
    def select_range(self, key):
        if self.anchor not in self.slots or key not in self.slots:
            self.sel = {key}
            return
        a, b = sorted((self.slots.index(self.anchor), self.slots.index(key)))
        self.sel = {k for k in self.slots[a:b + 1] if k}

    def on_press(self, _w, ev):
        self.grab_focus()
        key = self.item_at(ev.x, ev.y)
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        if ev.button == 3:
            if key and key not in self.sel:
                self.sel = {key}
            if not key:
                self.sel = set()
            self.queue_draw()
            self.item_menu(ev, key) if key else self.empty_menu(ev)
            return True
        if ev.button != 1:
            return False
        if ev.type == Gdk.EventType._2BUTTON_PRESS:
            if key:
                self.press = None
                self.open_items(self.ordered_sel() or [key])
            return True
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return True
        self.dnd = False
        if key:
            if ctrl:
                self.sel ^= {key}
                self.anchor = key
            elif shift:
                self.select_range(key)
            else:
                if key not in self.sel:
                    self.sel = {key}
                self.anchor = key
            self.press = (ev.x, ev.y, key, ctrl or shift)
        else:
            self.press = None
            base = set(self.sel) if ctrl else set()
            self.sel = set(base)
            self.rubber = [ev.x, ev.y, ev.x, ev.y, base]
        self.queue_draw()
        return True

    def on_motion(self, _w, ev):
        if self.rubber:
            self.rubber[2], self.rubber[3] = ev.x, ev.y
            x0, y0, x1, y1, base = self.rubber
            lx, hx, ly, hy = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
            hit = set()
            for i, k in enumerate(self.slots):
                if not k:
                    continue
                cx, cy = self.cell_xy(i)
                if cx + 12 < hx and cx + CELL_W - 12 > lx and cy + 6 < hy and cy + ICON_PX + 30 > ly:
                    hit.add(k)
            self.sel = base | hit
            self.queue_draw()
            return True
        if self.press and not self.dnd and (ev.state & Gdk.ModifierType.BUTTON1_MASK):
            x0, y0, key, _m = self.press
            if abs(ev.x - x0) + abs(ev.y - y0) > 8:
                self.dnd = True
                if key not in self.sel:
                    self.sel = {key}
                self.drag_keys = self.ordered_sel()
                tl = Gtk.TargetList.new([self.TARGET])
                self.drag_begin_with_coordinates(tl, Gdk.DragAction.COPY | Gdk.DragAction.MOVE, 1, ev,
                                                 int(ev.x), int(ev.y))
            return True
        key = self.item_at(ev.x, ev.y)
        if key is None and self.get_has_tooltip():
            self.set_tooltip_text(None)
        if key != self.hover:
            self.hover = key
            win = self.get_window()
            if win:
                win.set_cursor(Gdk.Cursor.new_from_name(Gdk.Display.get_default(), "pointer") if key else None)
            self.set_tooltip_text(self.items[key]["path"] if key in self.items else None) if key else self.set_tooltip_text(None)
            self.queue_draw()
        return True

    def on_leave(self, *_):
        # the tooltip text must go with the hover, or it pops up later over empty space
        self.set_tooltip_text(None)
        if self.hover is not None and not self.dnd:
            self.hover = None
            self.queue_draw()

    def on_release(self, _w, ev):
        if self.rubber:
            self.rubber = None
        elif self.press and not self.dnd and not self.press[3]:
            if len(self.sel) > 1:               # plain click inside a multi-selection selects just that item
                self.sel = {self.press[2]}
        self.press = None
        self.queue_draw()
        return True

    def on_key(self, _w, ev):
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        kv = ev.keyval
        sel = self.ordered_sel()
        if ctrl and kv in (Gdk.KEY_a, Gdk.KEY_A):
            self.sel = {k for k in self.slots if k}
        elif kv == Gdk.KEY_Escape:
            self.sel = set()
        elif kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and sel:
            self.open_items(sel)
        elif kv == Gdk.KEY_F2 and len(sel) == 1 and sel[0] != "@home":
            self.rename(self.items[sel[0]])
        elif kv == Gdk.KEY_Delete and sel:
            self.trash([self.items[k] for k in sel])
        else:
            return False
        self.queue_draw()
        return True

    # ---------------------------------------------------------- drag & drop
    def _set_drop(self, v):
        if v != self.drop_slot:
            self.drop_slot = v
            self.queue_draw()

    def on_drag_begin(self, _w, ctx):
        keys = self.drag_keys
        if not keys:
            return
        S = ICON_PX + 8
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, S + 14, S + 14)
        cr = cairo.Context(s)
        self.draw_icon(cr, self.items[keys[0]], 6, 8, size=ICON_PX, alpha=0.9)
        if len(keys) > 1:
            set_rgba(cr, ACCENT, 1)
            cr.arc(S + 2, 14, 11, 0, 2 * math.pi)
            cr.fill()
            text(cr, str(len(keys)), S + 2, 19, 13, C["on_sel"], bold=True, align="c")
        Gtk.drag_set_icon_surface(ctx, s)

    def on_drag_get(self, _w, ctx, data, info, time_):
        uris = [GLib.filename_to_uri(self.items[k]["path"]) for k in self.drag_keys
                if k in self.items and k != "@home"]
        data.set_uris(uris)

    def on_drag_end(self, _w, ctx):
        self.drag_keys, self.press, self.dnd = [], None, False
        self._set_drop(None)
        self.queue_draw()

    def on_drag_motion(self, _w, ctx, x, y, t):
        self._set_drop(self.slot_at(x, y))
        internal = Gtk.drag_get_source_widget(ctx) is self
        Gdk.drag_status(ctx, Gdk.DragAction.MOVE if internal else Gdk.DragAction.COPY, t)
        return True

    def on_drag_received(self, _w, ctx, x, y, data, _info, t):
        slot = self.slot_at(x, y)
        internal = Gtk.drag_get_source_widget(ctx) is self
        if internal and self.drag_keys and slot is not None:
            self.move_items(self.drag_keys, slot)
        elif not internal:
            key = self.item_at(x, y)
            dest = DESKTOP
            if key and self.items[key]["kind"] in ("dir", "home"):
                dest = self.items[key]["path"]
            for uri in data.get_uris() or []:
                src = Gio.File.new_for_uri(uri).get_path()
                if src and os.path.dirname(src.rstrip("/")) != dest:
                    if dest == DESKTOP and slot is not None:
                        self.pending[os.path.basename(src.rstrip("/"))] = slot
                    run(["cp", "-a", "--", src, dest + "/"])
        Gtk.drag_finish(ctx, True, False, t)
        self._set_drop(None)

    def move_items(self, keys, target):
        anchor = self.press[2] if self.press and self.press[2] in keys else keys[0]
        if anchor not in self.slots:
            return
        delta = target - self.slots.index(anchor)
        moving = [(self.slots.index(k), k) for k in keys if k in self.slots]
        slots = list(self.slots)
        for i, _k in moving:
            slots[i] = None
        for old, k in sorted(moving):
            self._place(slots, k, old + delta)
        self.slots = slots
        self.save_order()

    # ---------------------------------------------------------- actions
    def open_items(self, keys):
        for k in keys[:10]:
            it = self.items[k]
            run(["gio", "launch", it["path"]] if it["kind"] == "app" else ["xdg-open", it["path"]])

    @staticmethod
    def swatch_item(label, color, cb, current=False):
        item = Gtk.MenuItem()
        box = Gtk.Box(spacing=10)
        da = Gtk.DrawingArea()
        da.set_size_request(18, 18)

        def draw(_w, cr, color=color, current=current):
            if color:
                set_rgba(cr, color.lstrip("#"), 1)
                cr.arc(9, 9, 8, 0, 2 * math.pi)
                cr.fill()
                if current:
                    set_rgba(cr, C["ink"], 1)
                    cr.set_line_width(2)
                    cr.arc(9, 9, 11 - 3, 0, 2 * math.pi)
                    cr.stroke()
            else:
                set_rgba(cr, C["ink"], 0.7)
                cr.set_line_width(1.5)
                cr.arc(9, 9, 7.5, 0, 2 * math.pi)
                cr.stroke()
            return False
        da.connect("draw", draw)
        box.pack_start(da, False, False, 0)
        lbl = Gtk.Label(label=label)
        lbl.set_xalign(0)
        box.pack_start(lbl, True, True, 0)
        item.add(box)
        item.connect("activate", lambda *_: cb())
        return item

    def color_menu(self, targets):
        m = Gtk.Menu()
        for name, col in K.PALETTE:
            m.append(self.swatch_item(name, col, lambda c=col: self.set_style(targets, color=c)))
        m.append(Gtk.SeparatorMenuItem())
        m.append(self.swatch_item("Custom color…", None, lambda: self.custom_color(targets)))
        return m

    def glyph_menu(self, targets):
        m = Gtk.Menu()
        for g in K.GLYPH_NAMES:
            it = Gtk.MenuItem(label=g.capitalize() if g != "none" else "None (plain folder)")
            it.connect("activate", lambda _w, g=g: self.set_style(targets, glyph=g))
            m.append(it)
        return m

    def custom_color(self, targets):
        cur = self.styles.get(targets[0], {}).get("color", K.DEFAULT_COLOR)
        c = pick_color(cur)
        if c:
            self.set_style(targets, color=c)

    def set_style(self, keys, color=None, glyph=None, reset=False):
        st = dict(self.styles)
        for k in keys:
            if k not in self.items or self.items[k]["kind"] not in ("dir", "home"):
                continue
            if reset:
                st.pop(k, None)
            else:
                d = dict(st.get(k, {}))
                if color:
                    d["color"] = color
                if glyph is not None:
                    d["glyph"] = glyph
                st[k] = d
        self.styles = st
        save_styles(st)
        self._mt = self._mtimes()
        self.surfaces.clear()
        self.queue_draw()

    def item_menu(self, ev, key):
        keys = self.ordered_sel() or [key]
        its = [self.items[k] for k in keys]
        folders = [k for k in keys if self.items[k]["kind"] in ("dir", "home")]
        single = its[0] if len(its) == 1 else None
        m = Gtk.Menu()

        def add(label, cb):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda *_: cb())
            m.append(it)

        add("Open" if single else f"Open {len(its)} items", lambda: self.open_items(keys))
        if single and single["kind"] in ("dir", "home"):
            p = single["path"]
            add("Open in VS Code", lambda: run(["code", p]))
            add("cd here in the terminal", lambda: self.terminal.cd(p))
            add("Open in GNOME Terminal", lambda: run(["gnome-terminal", f"--working-directory={p}"]))
        m.append(Gtk.SeparatorMenuItem())
        add("Copy path" if single else "Copy paths", lambda: copy_text("\n".join(i["path"] for i in its)))
        if folders:
            m.append(Gtk.SeparatorMenuItem())
            ci = Gtk.MenuItem(label="Color")
            ci.set_submenu(self.color_menu(folders))
            m.append(ci)
            gi_ = Gtk.MenuItem(label="Icon")
            gi_.set_submenu(self.glyph_menu(folders))
            m.append(gi_)
            add("Reset look", lambda: self.set_style(folders, reset=True))
        real = [i for i in its if i["kind"] != "home"]
        if real:
            m.append(Gtk.SeparatorMenuItem())
            if single and single["kind"] != "home":
                add("Rename…", lambda: self.rename(single))
            add("Move to Trash", lambda: self.trash(real))
        m.show_all()
        m.popup_at_pointer(ev)

    def empty_menu(self, ev):
        m = Gtk.Menu()

        def add(label, cb):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda *_: cb())
            m.append(it)

        add("New Folder", self.new_folder)
        add("Open Desktop folder", lambda: run(["xdg-open", DESKTOP]))
        m.append(Gtk.SeparatorMenuItem())
        dirs = [k for k, v in self.items.items() if v["kind"] in ("dir", "home")]
        ci = Gtk.MenuItem(label="Color all folders")
        ci.set_submenu(self.color_menu(dirs))
        m.append(ci)
        ti = Gtk.MenuItem(label="Theme")
        tm = Gtk.Menu()
        for n, label in theme_names():
            it = Gtk.MenuItem(label=("●  " if n == THEME_NAME else "     ") + label)
            it.connect("activate", lambda _w, n=n: switch_theme(n))
            tm.append(it)
        ti.set_submenu(tm)
        m.append(ti)
        add("Group icons by type", self.group_icons)
        add("Compact icons", self.compact_icons)
        add("Select all", lambda: (setattr(self, "sel", {k for k in self.slots if k}), self.queue_draw()))
        m.append(Gtk.SeparatorMenuItem())
        add("Change Background…", lambda: run(["gnome-control-center", "background"]))
        m.show_all()
        m.popup_at_pointer(ev)

    def rename(self, it):
        old = os.path.basename(it["path"])
        new = ask_name("Rename", old)
        if new and new != old:
            try:
                os.rename(it["path"], os.path.join(DESKTOP, new))
                self.slots = [new if k == it["key"] else k for k in self.slots]
                if it["key"] in self.styles:
                    self.styles[new] = self.styles.pop(it["key"])
                    save_styles(self.styles)
                self.sel = {new}
                self.save_order()
            except OSError:
                pass

    def trash(self, its):
        for it in its:
            try:
                Gio.File.new_for_path(it["path"]).trash(None)
            except GLib.Error:
                pass
        self.sel = set()

    def new_folder(self):
        name = ask_name("New Folder", "New Folder")
        if name:
            try:
                os.makedirs(os.path.join(DESKTOP, name), exist_ok=False)
                self.sel = {name}
            except OSError:
                pass


# ----------------------------------------------------------------- launcher (Super+K / Ctrl+K)
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}


def calc(expr):
    """Tiny safe calculator: returns a number or None."""
    e = expr.replace("^", "**").replace("x", "*") if re.fullmatch(r"[\d\s\.\+\-\*/\(\)%\^x]+", expr) else expr
    if not re.fullmatch(r"[\d\s\.\+\-\*/\(\)%]+", e) or not re.search(r"\d\s*[\+\-\*/%]+\s*[\d\(]", e):
        return None

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 100:
                raise ValueError
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError
    try:
        return ev(ast.parse(e, mode="eval"))
    except Exception:
        return None


def match_score(q, *fields):
    ql, best = q.lower(), 0
    for f in fields:
        if not f:
            continue
        fl = f.lower()
        if fl == ql:
            best = max(best, 120)
        elif fl.startswith(ql):
            best = max(best, 100)
        elif any(w.startswith(ql) for w in re.split(r"[\s\-_./]+", fl)):
            best = max(best, 70)
        elif ql in fl:
            best = max(best, 40)
    return best


class Launcher:
    W, MAX_ROWS = 640, 8

    def __init__(self, terminal, icons):
        self.terminal, self.icons = terminal, icons
        self.shown_at = 0.0
        w = self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        w.set_title("Kiln Search")
        ctx = w.get_style_context()
        ctx.add_class("kiln")
        ctx.add_class("kiln-launcher")
        w.set_app_paintable(True)
        vis = w.get_screen().get_rgba_visual()
        if vis:
            w.set_visual(vis)
        w.set_decorated(False)
        w.set_resizable(False)
        w.set_keep_above(True)
        w.set_skip_taskbar_hint(True)
        w.set_skip_pager_hint(True)
        w.connect("draw", self.on_draw)
        w.connect("focus-out-event", self.on_focus_out)
        w.connect("key-press-event", self.on_window_key)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_size_request(self.W, -1)
        for m in ("start", "end", "top", "bottom"):
            getattr(outer, f"set_margin_{m}")(14)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Search anything…   apps · projects · files · 2+2 · >command · todo …")
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "system-search-symbolic")
        self.entry.get_style_context().add_class("kiln-search")
        self.entry.connect("changed", lambda e: self.refresh(e.get_text()))
        self.entry.connect("key-press-event", self.on_entry_key)
        outer.pack_start(self.entry, False, False, 0)
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.set_activate_on_single_click(True)
        self.list.get_style_context().add_class("kiln-results")
        self.list.connect("row-activated", lambda _l, row: self.activate(row.result))
        outer.pack_start(self.list, False, False, 0)
        w.add(outer)
        self.apps = []

    # --- window plumbing
    def on_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        a = self.win.get_allocation()
        card_bg(cr, 0, 0, a.width, a.height, 22, 0.94)
        return False

    def on_focus_out(self, *_):
        if time.time() - self.shown_at > 0.5:
            self.hide()
        return False

    def on_window_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def toggle(self):
        if self.win.get_visible():
            self.hide()
        else:
            self.show()

    def hide(self):
        self.win.hide()

    def show(self):
        self.apps = [a for a in Gio.AppInfo.get_all() if a.should_show()]
        self.shown_at = time.time()
        self.entry.set_text("")
        self.refresh("")
        disp = Gdk.Display.get_default()
        g = (disp.get_primary_monitor() or disp.get_monitor(0)).get_geometry()
        self.win.show_all()
        self.win.move(g.x + (g.width - self.W - 28) // 2, g.y + 120)
        gw = self.win.get_window()
        stamp = GdkX11.x11_get_server_time(gw) if gw is not None else Gdk.CURRENT_TIME
        self.win.present_with_time(stamp)
        self.entry.grab_focus()

    # --- keys
    def on_entry_key(self, _e, ev):
        kv = ev.keyval
        rows = self.list.get_children()
        cur = self.list.get_selected_row()
        idx = rows.index(cur) if cur in rows else 0
        if kv in (Gdk.KEY_Down, Gdk.KEY_Tab) and rows:
            self.list.select_row(rows[(idx + 1) % len(rows)])
            return True
        if kv in (Gdk.KEY_Up, Gdk.KEY_ISO_Left_Tab) and rows:
            self.list.select_row(rows[(idx - 1) % len(rows)])
            return True
        if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and cur is not None:
            self.activate(cur.result)
            return True
        return False

    # --- search
    def results(self, q):
        q = q.strip()
        out = []
        projects = load_projects()
        names = load_names()

        def proj_icon(p):
            col = (names.get(p["name"], (None, None))[1]) or hash_color(p["name"])
            return K.render(folder_svg_themed("#" + col, "none"), 40)

        if not q:
            for p in projects[:5]:
                out.append(dict(title=p["name"], sub=p["path"].replace(HOME, "~"), chip="Project",
                                pixbuf=proj_icon(p), action=lambda p=p: run(["code", p["path"]])))
            return out
        if q.startswith(">"):
            cmd = q[1:].strip()
            if cmd:
                out.append(dict(title=f"Run in terminal:  {cmd}", sub="Runs in the desktop terminal", chip="Command",
                                icon="utilities-terminal-symbolic",
                                action=lambda c=cmd: self.terminal.run_command(c)))
            return out
        tq = re.match(r"^themes?\s*(.*)$", q, re.I)
        if tq:
            want = tq.group(1).strip().lower()
            for n, label in theme_names():
                if not want or want in n or want in label.lower():
                    out.append(dict(title=f"Theme: {label}", sub="current theme" if n == THEME_NAME else "switch the whole desktop",
                                    chip="Theme", icon="preferences-desktop-appearance-symbolic",
                                    action=lambda n=n: switch_theme(n)))
            return out
        m = re.match(r"^(?:todo|t|\+)\s+(.+)$", q, re.I)
        if m:
            task = m.group(1)
            out.append(dict(title=f"Add task:  {task}", sub="Adds it to your Today card", chip="Todo",
                            icon="checkbox-checked-symbolic", action=lambda t=task: todo_add(t)))
        val = calc(q)
        if val is not None:
            txt = f"{val:.10g}"
            out.append(dict(title=f"= {txt}", sub=f"{q}   ·   Enter to copy", chip="Calc",
                            icon="accessories-calculator-symbolic", action=lambda t=txt: copy_text(t)))
        scored = []
        for a in self.apps:
            kws = " ".join(a.get_keywords() or []) if isinstance(a, Gio.DesktopAppInfo) else ""
            sc = match_score(q, a.get_display_name(), getattr(a, "get_generic_name", lambda: "")(), kws)
            if sc:
                scored.append((sc + 5, dict(title=a.get_display_name(), sub=a.get_description() or "Application",
                                            chip="App", gicon=a.get_icon(), action=lambda a=a: self.launch_app(a))))
        for p in projects:
            sc = match_score(q, p["name"], names.get(p["name"], (None, None))[0] or "", p["branch"])
            if sc:
                scored.append((sc + 8, dict(title=p["name"], sub=p["path"].replace(HOME, "~"), chip="Project",
                                            pixbuf=proj_icon(p), action=lambda p=p: run(["code", p["path"]]))))
        for k, it in self.icons.items.items():
            if k == "@home" or it["kind"] == "app":
                continue
            sc = match_score(q, it["label"])
            if sc:
                svg = folder_svg_themed(self.icons.styles.get(k, {}).get("color", K.DEFAULT_COLOR), "none") \
                    if it["kind"] == "dir" else K.doc_svg(it["ext"] or "file")
                scored.append((sc, dict(title=it["label"], sub="Desktop", chip="Folder" if it["kind"] == "dir" else "File",
                                        pixbuf=K.render(svg, 40), action=lambda it=it: run(["xdg-open", it["path"]]))))
        try:
            recent = sorted(Gtk.RecentManager.get_default().get_items(), key=lambda r: r.get_modified(), reverse=True)[:150]
        except Exception:
            recent = []
        for r in recent:
            uri = r.get_uri()
            if not uri.startswith("file://"):
                continue
            sc = match_score(q, r.get_display_name())
            if sc:
                path = GLib.filename_from_uri(uri)[0]
                if not os.path.exists(path) or any(path == p["path"] for p in projects):
                    continue
                scored.append((sc - 10, dict(title=r.get_display_name(), sub=path.replace(HOME, "~"), chip="Recent",
                                             gicon=r.get_gicon(), action=lambda u=uri: run(["xdg-open", u]))))
        scored.sort(key=lambda t: -t[0])
        out += [d for _s, d in scored]
        out = out[:self.MAX_ROWS - 1]
        ws = CFG.get("web_search") or DEFAULT_CONFIG["web_search"]
        out.append(dict(title=f"Search the web for “{q}”", sub=ws.get("name", "Web search"), chip="Web",
                        icon="web-browser-symbolic",
                        action=lambda q=q: open_url(ws.get("url", DEFAULT_CONFIG["web_search"]["url"]).replace(
                            "{q}", GLib.uri_escape_string(q, None, True)))))
        return out

    def refresh(self, q):
        for ch in self.list.get_children():
            self.list.remove(ch)
        for r in self.results(q):
            row = Gtk.ListBoxRow()
            row.result = r
            box = Gtk.Box(spacing=12)
            img = Gtk.Image()
            img.set_pixel_size(34)
            if r.get("pixbuf") is not None:
                img.set_from_pixbuf(r["pixbuf"])
            elif r.get("gicon") is not None:
                img.set_from_gicon(r["gicon"], Gtk.IconSize.DND)
                img.set_pixel_size(34)
            else:
                img.set_from_icon_name(r.get("icon", "text-x-generic"), Gtk.IconSize.DND)
                img.set_pixel_size(28)
            box.pack_start(img, False, False, 0)
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            t = Gtk.Label(label=r["title"])
            t.set_xalign(0)
            t.set_ellipsize(Pango.EllipsizeMode.END)
            t.set_max_width_chars(46)
            t.get_style_context().add_class("kiln-title")
            sb = Gtk.Label(label=r["sub"])
            sb.set_xalign(0)
            sb.set_ellipsize(Pango.EllipsizeMode.END)
            sb.set_max_width_chars(62)
            sb.get_style_context().add_class("kiln-sub")
            vb.pack_start(t, False, False, 0)
            vb.pack_start(sb, False, False, 0)
            box.pack_start(vb, True, True, 0)
            chip = Gtk.Label(label=r["chip"])
            chip.get_style_context().add_class("kiln-chip")
            box.pack_end(chip, False, False, 0)
            row.add(box)
            self.list.add(row)
        self.list.show_all()
        first = self.list.get_row_at_index(0)
        if first:
            self.list.select_row(first)

    def launch_app(self, app):
        fn = app.get_filename() if isinstance(app, Gio.DesktopAppInfo) else None
        if fn:
            run(["gio", "launch", fn])
        else:
            app.launch([], None)

    def activate(self, r):
        self.hide()
        try:
            r["action"]()
        except Exception:
            pass


# ----------------------------------------------------------------- now playing (any MPRIS player, e.g. YouTube Music in Brave)
ART_DIR = f"{CACHE}/art"
YTM_URL = "https://music.youtube.com"
# MPRIS names of YouTube Music desktop apps (Pear Desktop / th-ch "youtube-music", ytmdesktop)
YTM_ONLY = bool((CFG.get("now_playing") or {}).get("youtube_music_only"))
YTM_APPS = ("youtube-music", "youtubemusic", "youtube_music", "pear", "ytmdesktop")


_ytm_pids = {}


def _ytm_process(name):
    """Electron apps register as `chromium.instance<pid>`: look at the program behind the pid."""
    m = re.search(r"instance(\d+)$", name)
    if not m:
        return False
    pid = m.group(1)
    if pid not in _ytm_pids:
        try:
            exe = os.readlink(f"/proc/{pid}/exe").lower()
        except OSError:
            exe = ""
        _ytm_pids[pid] = "youtube-music" in exe or "youtube music" in exe or "pear" in exe
    return _ytm_pids[pid]


def is_ytm(name, url):
    """YouTube Music: its desktop app, or a browser tab on music.youtube.com."""
    name = name.lower()
    return any(a in name for a in YTM_APPS) or "music.youtube" in url.lower() or _ytm_process(name)


def open_ytm():
    """The YouTube Music desktop app if it is installed, else the website."""
    for exe in ("youtube-music", "pear-desktop", "ytmdesktop"):
        if shutil.which(exe):
            run([exe])
            return
    open_url(YTM_URL)


def fmt_time(s):
    s = max(0, int(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class NowPlaying(Gtk.DrawingArea):
    W, H = 390, 330
    FMT = "\t".join(["{{playerInstance}}", "{{playerName}}", "{{status}}", "{{title}}", "{{artist}}",
                     "{{album}}", "{{mpris:artUrl}}", "{{mpris:length}}", "{{position}}", "{{xesam:url}}"])

    def __init__(self):
        super().__init__()
        self.set_size_request(self.W, self.H)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK |
                        Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_press)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("leave-notify-event", lambda *_: self._hover(None))
        self.info = None
        self.art = {}            # url -> cairo surface | False (loading / failed)
        self.hover, self.hits = None, []
        os.makedirs(ART_DIR, exist_ok=True)
        self.poll()
        GLib.timeout_add(1500, self.poll)
        GLib.timeout_add(1000, self._tick)

    # --- data
    def poll(self):
        # asynchronous: a busy browser can take seconds to answer MPRIS, and the GTK main
        # loop must never wait for it (that froze every widget window)
        if getattr(self, "_polling", False):
            return True
        if DEMO:
            self._parse("")
            return True
        try:
            proc = Gio.Subprocess.new(["playerctl", "-a", "metadata", "--format", self.FMT],
                                      Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            self._parse("")
            return True
        self._polling = True
        cancel = Gio.Cancellable()
        kill = GLib.timeout_add(3000, lambda: (cancel.cancel(), proc.force_exit(), False)[-1])

        def done(pr, res):
            GLib.source_remove(kill) if not cancel.is_cancelled() else None
            self._polling = False
            try:
                _ok, out, _err = pr.communicate_utf8_finish(res)
            except GLib.Error:
                out = ""
            self._parse(out or "")
        proc.communicate_utf8_async(None, cancel, done)
        return True

    def _parse(self, out):
        players = []
        for line in out.splitlines():
            p = line.split("\t")
            if len(p) < 10 or not p[3].strip():
                continue
            ytm = is_ytm(p[0], p[9])
            if YTM_ONLY and not ytm:
                continue                      # VLC, YouTube videos, other players: not shown
            src = "YouTube Music" if ytm else p[1].split(".")[0].capitalize()
            try:
                length = int(p[7] or 0) / 1e6
                pos = int(p[8] or 0) / 1e6
            except ValueError:
                length, pos = 0, 0
            players.append(dict(inst=p[0], src=src, status=p[2], title=p[3].strip(), artist=p[4].strip(),
                                art=p[6].strip(), length=length, pos=pos, at=time.time()))
        def rank(x):
            return (x["status"] != "Playing", x["src"] != "YouTube Music")
        players.sort(key=rank)
        new = players[0] if players else None
        changed = (new or {}).get("title") != (self.info or {}).get("title") or \
                  (new or {}).get("status") != (self.info or {}).get("status")
        self.info = new
        if new and new["art"] and new["art"] not in self.art:
            self.load_art(new["art"])
        if changed or new:
            self.queue_draw()

    def _tick(self):
        if self.info and self.info["status"] == "Playing":
            self.queue_draw()
        return True

    def position(self):
        i = self.info
        if not i:
            return 0
        pos = i["pos"] + (time.time() - i["at"] if i["status"] == "Playing" else 0)
        return min(pos, i["length"]) if i["length"] else pos

    def load_art(self, url):
        self.art[url] = False

        def to_surface(path):
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 240, 240, True)
                self.art[url] = pixbuf_surface(pb)
            except Exception:
                self.art[url] = False
            self.queue_draw()
            return False

        if url.startswith("file://"):
            path = GLib.filename_from_uri(url)[0]
            if os.access(path, os.R_OK):
                GLib.idle_add(to_surface, path)
                return
            # Snap browsers (Brave, Chromium, Firefox) keep artwork in their private /tmp:
            # read it from inside that snap's own environment.
            import hashlib
            import threading
            snapname = ((self.info or {}).get("inst") or "").split(".")[0]
            dest = os.path.join(ART_DIR, hashlib.sha1(url.encode()).hexdigest() + ".img")

            def from_snap():
                try:
                    if not os.path.exists(dest):
                        with open(dest + ".part", "wb") as f:
                            subprocess.run(["snap", "run", "--shell", snapname, "-c", f"cat {shlex.quote(path)}"],
                                           stdout=f, stderr=subprocess.DEVNULL, timeout=10, env=clean_env())
                        if os.path.getsize(dest + ".part") > 0:
                            os.replace(dest + ".part", dest)
                        else:
                            os.remove(dest + ".part")
                    if os.path.exists(dest):
                        GLib.idle_add(to_surface, dest)
                except Exception:
                    pass
            if snapname:
                threading.Thread(target=from_snap, daemon=True).start()
        elif url.startswith(("http://", "https://")):
            import hashlib
            import threading
            import urllib.request
            dest = os.path.join(ART_DIR, hashlib.sha1(url.encode()).hexdigest() + ".img")

            def fetch():
                try:
                    if not os.path.exists(dest):
                        with urllib.request.urlopen(url, timeout=6) as r:
                            data = r.read(4 * 1024 * 1024)
                        with open(dest, "wb") as f:
                            f.write(data)
                    GLib.idle_add(to_surface, dest)
                except Exception:
                    pass
            threading.Thread(target=fetch, daemon=True).start()

    # --- control
    def ctl(self, *args):
        if self.info:
            run(["playerctl", "-p", self.info["inst"], *args])
            GLib.timeout_add(300, lambda: (self.poll(), False)[1])

    def _hit(self, px, py):
        for (x, y, w, h), key in reversed(self.hits):
            if x <= px < x + w and y <= py < y + h:
                return key
        return None

    def _hover(self, v):
        if v != self.hover:
            self.hover = v
            win = self.get_window()
            if win:
                win.set_cursor(Gdk.Cursor.new_from_name(Gdk.Display.get_default(), "pointer") if v else None)
            self.queue_draw()

    def on_motion(self, _w, ev):
        self._hover(self._hit(ev.x, ev.y))

    def on_press(self, _w, ev):
        key = self._hit(ev.x, ev.y)
        if key is None or ev.button != 1:
            return False
        if key == "open":
            open_ytm()
        elif not self.info:
            return True
        elif key in ("play", "art"):
            self.ctl("play-pause")
        elif key == "prev":
            self.ctl("previous")
        elif key == "next":
            self.ctl("next")
        elif key == "seek" and self.info["length"]:
            frac = max(0.0, min(1.0, (ev.x - 20) / (self.W - 40)))
            self.ctl("position", f"{frac * self.info['length']:.1f}")
        return True

    # --- drawing
    def on_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        self.hits = []
        W = self.W
        i = self.info
        card_bg(cr, 0, 0, W, self.H, 20)
        glyph(cr, "music", 20, 16, 22, C["acc2"])
        text(cr, "Now Playing", 52, 34, 16, C["text"], bold=True)
        if i:
            pill(cr, W - 20, 31, i["src"], 11, "ff6b8a" if i["src"] == "YouTube Music" else C["acc"], align="r")

        # artwork
        ax, ay, asz = 20, 56, 136
        self.hits.append(((ax, ay, asz, asz), "art"))
        surf = self.art.get(i["art"]) if i and i["art"] else None
        cr.save()
        rrect(cr, ax, ay, asz, asz, 14)
        cr.clip()
        if surf:
            sw, sh = surf.get_width(), surf.get_height()
            k = max(asz / sw, asz / sh)
            cr.translate(ax + (asz - sw * k) / 2, ay + (asz - sh * k) / 2)
            cr.scale(k, k)
            pat = cairo.SurfacePattern(surf)
            pat.set_filter(cairo.FILTER_BEST)
            cr.set_source(pat)
            cr.paint()
        else:
            g = cairo.LinearGradient(ax, ay, ax + asz, ay + asz)
            g.add_color_stop_rgb(0, *rgb(C["ph1"]))
            g.add_color_stop_rgb(1, *rgb(C["ph2"]))
            cr.set_source(g)
            cr.paint()
        cr.restore()
        if not surf:
            glyph(cr, "music", ax + asz / 2 - 20, ay + asz / 2 - 20, 40, "ffffff", alpha=0.55)

        # title / artist
        tx, tw = ax + asz + 16, W - (ax + asz + 16) - 18
        lay = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family(FONT)
        fd.set_weight(Pango.Weight.BOLD)
        fd.set_absolute_size(15 * Pango.SCALE)
        lay.set_font_description(fd)
        lay.set_width(int(tw * Pango.SCALE))
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay.set_ellipsize(Pango.EllipsizeMode.END)
        lay.set_height(-3)
        lay.set_text(i["title"] if i else "Nothing playing", -1)
        set_rgba(cr, C["text"] if i else C["dim"], 1)
        cr.move_to(tx, ay + 2)
        PangoCairo.show_layout(cr, lay)
        _lw, lh = lay.get_pixel_size()
        sub = (i["artist"] or "") if i else "Play something on YouTube Music"
        lay2 = PangoCairo.create_layout(cr)
        fd2 = Pango.FontDescription()
        fd2.set_family(FONT)
        fd2.set_absolute_size(12.5 * Pango.SCALE)
        lay2.set_font_description(fd2)
        lay2.set_width(int(tw * Pango.SCALE))
        lay2.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay2.set_ellipsize(Pango.EllipsizeMode.END)
        lay2.set_height(-2)
        lay2.set_text(sub, -1)
        set_rgba(cr, C["dim"], 1)
        cr.move_to(tx, ay + lh + 8)
        PangoCairo.show_layout(cr, lay2)

        # progress
        py = 216
        frac = (self.position() / i["length"]) if i and i["length"] else 0
        self.hits.append(((14, py - 10, W - 28, 20), "seek"))
        rrect(cr, 20, py - 2, W - 40, 4, 2)
        set_rgba(cr, C["ink"], 0.10)
        cr.fill()
        if frac > 0:
            rrect(cr, 20, py - 2, max(4, (W - 40) * frac), 4, 2)
            set_rgba(cr, C["acc"], 1)
            cr.fill()
            if self.hover == "seek":
                cr.new_path()
                set_rgba(cr, C["ink"], 1)
                cr.arc(20 + (W - 40) * frac, py, 6, 0, 2 * math.pi)
                cr.fill()
        if i and i["length"]:
            text(cr, fmt_time(self.position()), 20, py + 20, 11, C["dim"])
            text(cr, fmt_time(i["length"]), W - 20, py + 20, 11, C["dim"], align="r")

        # controls
        cx, cy = W / 2, 270
        dim_a = 1 if i else 0.35
        for key, dx in (("prev", -62), ("next", 62)):
            self.hits.append(((cx + dx - 18, cy - 18, 36, 36), key))
            if self.hover == key and i:
                cr.new_path()
                set_rgba(cr, C["ink"], 0.08)
                cr.arc(cx + dx, cy, 18, 0, 2 * math.pi)
                cr.fill()
            set_rgba(cr, C["text"], dim_a)
            s = -1 if key == "prev" else 1
            bx = cx + dx
            cr.new_path()
            cr.move_to(bx - 5 * s, cy - 8)
            cr.line_to(bx + 6 * s, cy)
            cr.line_to(bx - 5 * s, cy + 8)
            cr.close_path()
            cr.fill()
            cr.rectangle(bx + 6 * s - (2 if s > 0 else 0), cy - 8, 2.4, 16)
            cr.fill()
        self.hits.append(((cx - 26, cy - 26, 52, 52), "play"))
        cr.new_path()
        set_rgba(cr, C["acc"], (1 if self.hover == "play" else 0.92) * dim_a)
        cr.arc(cx, cy, 25, 0, 2 * math.pi)
        cr.fill()
        set_rgba(cr, C["on_acc"], 1)
        if i and i["status"] == "Playing":
            cr.rectangle(cx - 7, cy - 9, 5, 18)
            cr.rectangle(cx + 2, cy - 9, 5, 18)
            cr.fill()
        else:
            cr.move_to(cx - 5, cy - 10)
            cr.line_to(cx + 10, cy)
            cr.line_to(cx - 5, cy + 10)
            cr.close_path()
            cr.fill()

        # footer
        if i:
            text(cr, f"{i['status'].lower()} · {i['src']}", cx, 318, 11.5, C["dim"], align="c")
        else:
            label = "Open YouTube Music"
            lay3 = PangoCairo.create_layout(cr)
            fd3 = Pango.FontDescription()
            fd3.set_family(FONT)
            fd3.set_absolute_size(11.5 * Pango.SCALE)
            lay3.set_font_description(fd3)
            lay3.set_text(label, -1)
            pw = lay3.get_pixel_size()[0] + 14
            px0 = cx - pw / 2
            pill(cr, px0, 318, label, 11.5, "ff6b8a", bg_alpha=0.30 if self.hover == "open" else 0.16)
            self.hits.append(((px0 - 4, 300, pw + 8, 26), "open"))
        return False


# ----------------------------------------------------------------- quick project bar (bottom strip)
# ----------------------------------------------------------------- dev servers bar
KNOWN_SERVICES = {5432: "postgres", 3306: "mysql", 6379: "redis", 27017: "mongodb", 11434: "ollama",
                  9200: "elasticsearch", 5672: "rabbitmq", 8888: "jupyter", 1433: "sql server"}
# desktop apps that open local ports but are not "dev servers"
IGNORE_PROCS = {"brave", "chrome", "chromium", "firefox", "code", "electron", "claude", "spotify",
                "slack", "discord", "lark", "steam", "kdeconnectd", "zoom", "telegram-desktop",
                "gnome-shell", "gsd-sharing", "evolution-addre", "goa-daemon", "ibus-daemon"}
FRAMEWORKS = [("next", "next"), ("vite", "vite"), ("runserver", "django"), ("uvicorn", "uvicorn"),
              ("gunicorn", "gunicorn"), ("flask", "flask"), ("nodemon", "nodemon"),
              ("react-scripts", "react"), ("nest", "nest"), ("artisan", "laravel"), ("rails", "rails"),
              ("http.server", "http.server"), ("webpack", "webpack"), ("astro", "astro"),
              ("nuxt", "nuxt"), ("expo", "expo"), ("flutter", "flutter"), ("hugo", "hugo")]


RUNNING_HIDE = f"{APP}/running-hide.txt"     # process / service names kept off the Running list


def running_hidden():
    return {l.strip().lower() for l in read_lines_keep(RUNNING_HIDE) if l.strip() and not l.startswith("#")}


def scan_dev():
    """Listening TCP ports owned by this user (+ well-known local services) and Docker containers."""
    if DEMO:
        return DEMO.get("servers", []), DEMO.get("containers")
    servers = {}
    hidden = running_hidden()
    try:
        out = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        host, _, port = parts[3].rpartition(":")
        try:
            port = int(port)
        except ValueError:
            continue
        if port in servers:          # the IPv4 and IPv6 sockets of one server
            continue
        m = re.search(r'\("([^"]+)",pid=(\d+)', line)
        if not m:
            if port in KNOWN_SERVICES and KNOWN_SERVICES[port] not in hidden:
                servers[port] = dict(port=port, label=KNOWN_SERVICES[port], pid=None, cwd=None, kind="service",
                                     proc=KNOWN_SERVICES[port])
            continue
        name, pid = m.group(1), int(m.group(2))
        if name.lower() in IGNORE_PROCS or name.lower() in hidden or \
                (port >= 32768 and port not in KNOWN_SERVICES):
            continue
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            cwd = None
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            cmd = name
        fw = KNOWN_SERVICES.get(port) or next((lab for key, lab in FRAMEWORKS if key in cmd), name)
        proj = os.path.basename(cwd) if cwd and cwd not in (HOME, "/") else ""
        if fw.lower() in hidden:
            continue
        servers[port] = dict(port=port, label=f"{proj} · {fw}" if proj and proj != fw else fw,
                             pid=pid, cwd=cwd, kind="server", local=host.strip("[]") in ("127.0.0.1", "::1"),
                             proc=name)
    containers = None
    if shutil.which("docker"):
        containers = []
        try:
            out = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Ports}}"],
                                 capture_output=True, text=True, timeout=4).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        for line in out.splitlines():
            p = (line.split("\t") + ["", "", ""])[:3]
            ports = sorted({int(x) for x in re.findall(r":(\d+)->", p[2])})
            containers.append(dict(name=p[0], running=p[1] == "running", ports=ports))
        containers.sort(key=lambda c: (not c["running"], c["name"]))
    return sorted(servers.values(), key=lambda s: (s["kind"] != "server", s["port"])), containers


PIN_FILE = f"{APP}/current-project.txt"
MODE_W = 270                       # room kept at the right end of the work bar for the mode switcher
MODES = ("overview", "coding", "focus")
CMD_FILE = f"{CACHE}/cmd"          # `kiln mode ...` / `kiln test` drop their request here


def _git(path, *args, timeout=3, raw=False):
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return None
        return r.stdout if raw else r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _find_moved(base):
    """Where a folder named `base` lives now: a known repo, else anywhere 3 levels under Desktop."""
    hit = next((r["path"] for r in load_projects() if os.path.basename(r["path"]) == base), None)
    if hit:
        return hit
    top = os.path.join(HOME, "Desktop")
    for root, dirs, _files in os.walk(top):
        if base in dirs:
            return os.path.join(root, base)
        if root[len(top):].count(os.sep) >= 2:
            dirs[:] = []
        else:
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "venv", ".venv")]
    return None


def _claude_last_cwd():
    """(time of last message, project dir) of the newest Claude Code session in a real folder.
    Folders moved since (the Desktop reorganisation) are found again by name."""
    root = os.path.expanduser("~/.claude/projects")
    files = []
    try:
        dirs = [d for d in os.listdir(root)
                if "scratch-workspaces" not in d and d != re.sub(r"[^A-Za-z0-9]", "-", HOME)]
    except OSError:
        return None
    for d in dirs:
        try:
            for f in os.listdir(os.path.join(root, d)):
                if f.endswith(".jsonl"):
                    fp = os.path.join(root, d, f)
                    files.append((os.path.getmtime(fp), fp))
        except OSError:
            pass
    best = None
    for _mt, fp in sorted(files, reverse=True)[:25]:
        t = session_time(fp)
        if not t or (best and t <= best[0]):
            continue
        try:
            with open(fp, "rb") as fh:
                cwd = re.search(r'"cwd":"([^"]+)"', fh.read(200000).decode("utf-8", "replace"))
        except OSError:
            continue
        if not cwd:
            continue
        path = cwd.group(1)
        if not os.path.isdir(path):
            path = _find_moved(os.path.basename(path))
        if path:
            best = (t, path)
    return best


def _repo_root(path):
    top = _git(path, "rev-parse", "--show-toplevel", timeout=2) or path
    return None if top in (HOME, "/", os.path.join(HOME, "Desktop")) else top


def _vscode_windows():
    """(folder name, focused?) of every open VS Code window (it runs on X11 here)."""
    try:
        ids = subprocess.run(["xdotool", "search", "--name", r" - Visual Studio Code$"],
                             capture_output=True, text=True, timeout=2).stdout.split()
        act = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True,
                             timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for wid in ids:
        try:
            name = subprocess.run(["xdotool", "getwindowname", wid], capture_output=True, text=True,
                                  timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        parts = name.rsplit(" - Visual Studio Code", 1)[0].split(" - ")
        folder = parts[-1].strip().lstrip("● ").strip()
        if folder:
            out.append((folder, wid == act))
    return out


def _vscode_paths():
    """Folders VS Code knows about (command lines of running instances + workspace storage)."""
    paths = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        if argv and argv[0].endswith(b"/code") and not any(a.startswith(b"--type=") for a in argv):
            paths += [a.decode(errors="replace") for a in argv[1:] if a.startswith(b"/")]
    import glob
    from urllib.parse import unquote, urlparse
    for wj in glob.glob(os.path.expanduser("~/.config/Code/User/workspaceStorage/*/workspace.json")):
        try:
            uri = json.load(open(wj)).get("folder", "")
        except (OSError, ValueError):
            continue
        if uri.startswith("file://"):
            paths.append(unquote(urlparse(uri).path))
    return [p for p in paths if os.path.isdir(p)]


def open_projects(last_active):
    """Projects open right now: VS Code windows, running Claude Code sessions, terminals sitting
    in a repo, running dev servers. Newest activity first. `last_active` (path -> time) is kept
    by the caller so a window that was focused a minute ago still ranks above an idle one."""
    now = time.time()
    if DEMO:
        return [dict(path=p, name=os.path.basename(p), sources={"vscode"}, t=now - 60 * i)
                for i, p in enumerate(DEMO.get("projects", []))]
    found = {}

    def add(path, src, t=0):
        root = _repo_root(path) if path and os.path.isdir(path) else None
        if not root or "scratch-workspaces" in root:
            return
        p = found.setdefault(root, dict(path=root, name=os.path.basename(root), sources=set(), t=0))
        p["sources"].add(src)
        p["t"] = max(p["t"], t)

    known = _vscode_paths()
    for folder, focused in _vscode_windows():
        path = next((k for k in known if os.path.basename(k) == folder), None) or _find_moved(folder)
        add(path, "vscode", now if focused else 0)
    procs_root = os.path.expanduser("~/.claude/projects")
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            exe = os.path.basename(os.readlink(f"/proc/{pid}/exe"))
            if exe not in ("claude", "bash", "zsh", "fish"):
                continue
            cwd = os.readlink(f"/proc/{pid}/cwd")
            if exe == "claude":
                enc = re.sub(r"[^A-Za-z0-9]", "-", cwd)
                d = os.path.join(procs_root, enc)
                t = max((os.path.getmtime(os.path.join(d, f)) for f in os.listdir(d) if f.endswith(".jsonl")),
                        default=0) if os.path.isdir(d) else 0
                add(cwd, "claude", t)
            elif os.readlink(f"/proc/{pid}/fd/0").startswith("/dev/pts/"):
                add(cwd, "terminal")          # an interactive shell, not a tool's `bash -c`
        except OSError:
            continue
    out = []
    for p in found.values():
        p["t"] = max(p["t"], last_active.get(p["path"], 0))
        last_active[p["path"]] = p["t"]
        out.append(p)
    weight = {"vscode": 3, "claude": 2, "terminal": 1}
    out.sort(key=lambda p: (p["t"], sum(weight.get(s, 0) for s in p["sources"])), reverse=True)
    return out


def current_project(term_cwd=None):
    """The project being worked on: a pinned one, else whichever was touched last of the
    newest VS Code workspace (its state.vscdb) and the newest Claude Code session; else a
    repo open in the terminal, else the latest commit."""
    try:
        pin = open(PIN_FILE).read().strip()
        if pin and os.path.isdir(pin):
            return pin, "pinned"
    except OSError:
        pass
    cands = []
    import glob
    from urllib.parse import unquote, urlparse
    for wj in glob.glob(os.path.expanduser("~/.config/Code/User/workspaceStorage/*/workspace.json")):
        try:
            uri = json.load(open(wj)).get("folder", "")
        except (OSError, ValueError):
            continue
        if not uri.startswith("file://"):
            continue
        path = unquote(urlparse(uri).path)
        if not os.path.isdir(path):
            continue
        d = os.path.dirname(wj)
        db = os.path.join(d, "state.vscdb")
        cands.append((os.path.getmtime(db if os.path.exists(db) else d), path, "vscode"))
    cl = _claude_last_cwd()
    if cl:
        cands.append((cl[0], cl[1], "claude"))
    for _t, path, src in sorted(cands, reverse=True):
        top = _git(path, "rev-parse", "--show-toplevel") or path
        if top != HOME:
            return top, src
    if term_cwd:
        top = _git(term_cwd, "rev-parse", "--show-toplevel")
        if top and top != HOME:
            return top, "terminal"
    rows = load_projects()
    if rows:
        return rows[0]["path"], "recent"
    return None, None


def github_url(path):
    url = _git(path, "remote", "get-url", "origin") or ""
    m = re.match(r"(?:git@github\.com:|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return f"https://github.com/{m.group(1)}" if m else None


def _status_code(xy):
    if "?" in xy:
        return "?"
    for ch in ("U", "D", "A", "R"):
        if ch in xy:
            return ch
    return "M"


def scan_project(path, with_pr):
    if not path:
        return None
    info = dict(path=path, name=os.path.basename(path), branch=_git(path, "rev-parse", "--abbrev-ref", "HEAD"))
    st = _git(path, "status", "--porcelain", timeout=6, raw=True)
    lines = [l for l in (st or "").splitlines() if len(l) > 3]
    info["dirty"] = len(lines)
    info["files"] = [(_status_code(l[:2]), l[3:].split(" -> ")[-1].strip('"')) for l in lines[:60]]
    info["log"] = []
    for l in (_git(path, "log", "-8", "--format=%h%x09%ct%x09%an%x09%s") or "").splitlines():
        parts = l.split("\t", 3)
        if len(parts) == 4 and parts[1].isdigit():
            info["log"].append((parts[0], int(parts[1]), parts[2], parts[3]))
    ab = _git(path, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    info["behind"], info["ahead"] = (int(x) for x in ab.split()) if ab and len(ab.split()) == 2 else (0, 0)
    lc = _git(path, "log", "-1", "--format=%ct%x09%s")
    if lc and "\t" in lc:
        t, msg = lc.split("\t", 1)
        info["commit"], info["commit_t"] = msg, int(t)
    info["gh"] = github_url(path)
    info["test"] = test_command(path)
    try:
        info["claude"] = claude_tasks(path) if INTEGRATIONS.get("claude_code", True) else None
    except Exception:
        info["claude"] = None
    info["pr"] = None
    if with_pr and info["gh"] and info["branch"] not in (None, "HEAD", "main", "master") and shutil.which("gh"):
        try:
            r = subprocess.run(["gh", "pr", "view", info["branch"], "--repo", info["gh"],
                                "--json", "number,url,state,statusCheckRollup"],
                               capture_output=True, text=True, timeout=8)
            if r.returncode == 0:
                d = json.loads(r.stdout)
                checks = [c.get("conclusion") or c.get("state") or c.get("status") for c in d.get("statusCheckRollup") or []]
                ci = "fail" if any(c in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED") for c in checks) else \
                     "pending" if any(c in ("PENDING", "IN_PROGRESS", "QUEUED", "EXPECTED") for c in checks) else \
                     "ok" if checks else None
                info["pr"] = dict(number=d["number"], url=d["url"], state=d.get("state"), ci=ci)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            pass
    return info


def since(ts):
    d = max(0, time.time() - ts)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if d >= n:
            return f"{int(d // n)}{unit}"
    return "now"


class WorkBar(Gtk.DrawingArea):
    """Bottom strip: the project being worked on (branch, changes, last commit, PR / CI)
    and everything running locally (dev servers, services, Docker)."""
    W, H = 1792, 50

    def __init__(self, terminal):
        super().__init__()
        self.terminal = terminal
        self.set_size_request(self.W, self.H)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK |
                        Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_press)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("leave-notify-event", lambda *_: self._hover(None))
        self.hits, self.hover = [], None
        self.servers, self.containers, self.proj, self.source = None, None, None, None
        self._busy, self._n, self._pr_t, self._pr = False, 0, 0, None
        self.opened, self.chosen, self._last_active = [], None, {}   # open projects / user's pick
        self.mode = "overview"
        self.on_mode = None                                           # set by main(): switches the desktop mode
        self.tests = _json_file(f"{CACHE}/tests.json") or {}          # last test run per project
        for v in self.tests.values():
            if v.get("status") == "running":                          # left over from a previous session
                v["status"] = "fail"
        self.refresh()
        GLib.timeout_add_seconds(every(4), self.refresh)

    # everything is gathered off the main loop (git / ss / gh can be slow)
    def refresh(self):
        if self._busy:
            self._again = True        # e.g. a project was picked mid-scan: run again right after
            return True
        self._busy = True
        self._n += 1
        full = self._n % 3 == 1                       # project git info every ~12 s
        with_pr = time.time() - self._pr_t > 120      # GitHub PR / CI every 2 min
        if with_pr:
            self._pr_t = time.time()
        try:
            tab = self.terminal.tabs[self.terminal.active]
            term_cwd = tab.get("cwd")
        except (AttributeError, IndexError, KeyError):
            term_cwd = None
        import threading

        cur = self.proj["path"] if self.proj else None
        chosen = self.chosen

        def work():
            res = {}
            try:
                res["dev"] = scan_dev()
            except Exception:
                res["dev"] = ([], None)
            try:
                opened = open_projects(self._last_active)
            except Exception:
                opened = []
            res["open"] = opened
            # pinned > the user's pick among the open ones > the most recently active open one
            # > the old guess (VS Code / Claude history) when nothing is open
            path, src = None, None
            try:
                pin = open(PIN_FILE).read().strip()
                if pin and os.path.isdir(pin):
                    path, src = pin, "pinned"
            except OSError:
                pass
            paths = [p["path"] for p in opened]
            if not path and chosen in paths:
                path, src = chosen, "chosen"
            if not path and opened:
                path, src = opened[0]["path"], "open"
            try:
                if not path:
                    path, src = current_project(term_cwd)
                if full or path != cur:
                    res["proj"] = (scan_project(path, with_pr or path != cur), src, with_pr or path != cur)
            except Exception:
                pass
            GLib.idle_add(self._done, res)
        threading.Thread(target=work, daemon=True).start()
        return True

    def _done(self, res):
        self._busy = False
        if getattr(self, "_again", False):
            self._again = False
            GLib.idle_add(lambda: (self.refresh(), False)[1])
        old = (self.servers, self.containers, self.proj)
        self.servers, self.containers = res["dev"]
        WORK["dev"] = res["dev"]
        old_open = [p["path"] for p in self.opened]
        self.opened = res.get("open", [])
        if self.chosen and self.chosen not in [p["path"] for p in self.opened]:
            self.chosen = None                    # the picked project was closed: back to automatic
        if old_open != [p["path"] for p in self.opened]:
            self.queue_draw()
        if "proj" in res:
            proj, self.source, fresh_pr = res["proj"]
            if proj:
                # keep the last PR result between the slower GitHub checks
                if fresh_pr or not self.proj or self.proj["path"] != proj["path"]:
                    self._pr = proj["pr"]
                proj["pr"] = self._pr
            self.proj = proj
            WORK["proj"] = proj
        if old != (self.servers, self.containers, self.proj):
            self.queue_draw()
        return False

    def run_tests(self, path):
        cmd = test_command(path)
        if not cmd or self.tests.get(path, {}).get("status") == "running":
            return
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(path))
        log = f"{CACHE}/test-{safe}.log"
        self.tests[path] = dict(status="running", t=time.time(), cmd=cmd, log=log)
        self.queue_draw()
        import threading

        def work():
            t0 = time.time()
            status = "fail"
            try:
                with open(log, "w", encoding="utf-8") as lf:
                    lf.write(f"$ {cmd}\n\n")
                    lf.flush()
                    proc = subprocess.Popen(["bash", "-lic", cmd], cwd=path, stdout=lf, stderr=subprocess.STDOUT,   # -i: nvm etc. live in .bashrc
                                            stdin=subprocess.DEVNULL, start_new_session=True,
                                            env=dict(os.environ, CI="true", NO_COLOR="1"))
                    try:
                        status = "pass" if proc.wait(timeout=900) == 0 else "fail"
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            GLib.idle_add(self._tests_done, path, status, time.time() - t0)
        threading.Thread(target=work, daemon=True).start()

    def _tests_done(self, path, status, dur):
        self.tests[path] = dict(self.tests.get(path, {}), status=status, t=time.time(), dur=dur)
        try:
            with open(f"{CACHE}/tests.json", "w", encoding="utf-8") as f:
                json.dump(self.tests, f)
        except OSError:
            pass
        ok = status == "pass"
        run(["notify-send", "-a", "Kiln", "-i", "emblem-ok-symbolic" if ok else "dialog-warning-symbolic",
             "Tests passed" if ok else "Tests failed", f"{os.path.basename(path)} · {dur:.0f}s"])
        self.queue_draw()
        return False

    def hide(self, name):
        ls = read_lines_keep(RUNNING_HIDE)
        if name.lower() not in {l.strip().lower() for l in ls}:
            write_lines(RUNNING_HIDE, ls + [name])
        self.refresh()

    def pin(self, path):
        try:
            if path:
                with open(PIN_FILE, "w") as f:
                    f.write(path + "\n")
            elif os.path.exists(PIN_FILE):
                os.remove(PIN_FILE)
        except OSError:
            pass
        self._n, self._pr_t = 0, 0
        self.refresh()

    def _hit(self, px, py):
        for (x, y, w, h), key in reversed(self.hits):
            if x <= px < x + w and y <= py < y + h:
                return key
        return None

    def _hover(self, v):
        if v != self.hover:
            self.hover = v
            win = self.get_window()
            if win:
                win.set_cursor(Gdk.Cursor.new_from_name(Gdk.Display.get_default(), "pointer") if v else None)
            self.queue_draw()

    def on_motion(self, _w, ev):
        self._hover(self._hit(ev.x, ev.y))

    def _in_tab(self, path, cmd):
        self.terminal.new_tab(path or os.path.expanduser("~"))
        GLib.timeout_add(400, lambda: (self.terminal.run_command(cmd), False)[1])

    @staticmethod
    def _stop(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    def project_menu(self, ev):
        p = self.proj
        items = []
        if p:
            path, gh = p["path"], p["gh"]
            items += [("Open in VS Code", lambda: run(["code", path])),
                      ("New terminal tab here", lambda: self.terminal.new_tab(path)),
                      ("git status in a terminal tab", lambda: self._in_tab(path, "git status")),
                      ("Open folder", lambda: run(["nautilus", path]))]
            if gh:
                items += [("Open on GitHub", lambda: run(["xdg-open", gh]))]
            items += [(None, None), ("Copy path", lambda: copy_text(path)), (None, None)]
        m = Gtk.Menu()
        for label, cb in items:
            if label is None:
                m.append(Gtk.SeparatorMenuItem())
                continue
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda _w, cb=cb: cb())
            m.append(it)
        sw = Gtk.MenuItem(label="Switch project")
        sub = Gtk.Menu()
        for r in load_projects()[:12]:
            it = Gtk.MenuItem(label=("●  " if p and r["path"] == p["path"] else "     ") + r["name"])
            it.connect("activate", lambda _w, path=r["path"]: self.pin(path))
            sub.append(it)
        sw.set_submenu(sub)
        m.append(sw)
        if self.source == "pinned":
            it = Gtk.MenuItem(label="Unpin (follow VS Code again)")
            it.connect("activate", lambda *_: self.pin(None))
            m.append(it)
        elif p:
            it = Gtk.MenuItem(label="Pin this project")
            it.connect("activate", lambda *_: self.pin(p["path"]))
            m.append(it)
        m.show_all()
        m.popup_at_pointer(ev)

    def on_press(self, _w, ev):
        key = self._hit(ev.x, ev.y)
        if not key or ev.button not in (1, 3):
            return False
        kind, it = key
        p = self.proj
        if kind == "mode":
            if ev.button == 1 and self.on_mode:
                self.on_mode(it)
            return True
        if kind == "pick":
            if ev.button == 1:
                self.chosen = it
                self._pr_t = 0
                self.refresh()
            else:
                self.project_menu(ev)
            return True
        if kind == "proj":
            if ev.button == 1 and p:
                run(["code", p["path"]])
            else:
                self.project_menu(ev)
            return True
        if kind == "branch":
            self.terminal.new_tab(p["path"]) if ev.button == 1 else self.project_menu(ev)
            return True
        if kind == "dirty":
            self._in_tab(p["path"], "git status") if ev.button == 1 else self.project_menu(ev)
            return True
        if kind == "commit":
            if ev.button == 1 and p["gh"]:
                run(["xdg-open", f"{p['gh']}/commits/{p['branch']}"])
            else:
                self.project_menu(ev)
            return True
        if kind == "pr":
            run(["xdg-open", it["url"]])
            return True
        if kind == "test":
            path = it["path"]
            if ev.button == 1:
                self.run_tests(path)
            else:
                t = self.tests.get(path, {})
                items = [("Run the tests", lambda: self.run_tests(path)),
                         ("Run them in a terminal tab", lambda: self._in_tab(path, it["test"]))]
                if t.get("log") and os.path.exists(t["log"]):
                    items.append(("Show the last output", lambda: self._in_tab(
                        path, f"less -R +G {shlex.quote(t['log'])}")))
                items += [(None, None), ("Copy the command", lambda: copy_text(it["test"]))]
                popup_menu(ev, items)
            return True
        return self.dev_action(ev, key)

    def dev_action(self, ev, key):
        """Click / right-click on a dev server or container (used by the bar and the Running card)."""
        kind, it = key
        url = f"http://localhost:{it['port']}" if kind == "srv" else \
            (f"http://localhost:{it['ports'][0]}" if it["ports"] else None)
        if kind == "srv":
            if ev.button == 1 and it["kind"] == "server":
                run(["xdg-open", url])
                return True
            items = [("Open in browser", lambda: run(["xdg-open", url])),
                     ("Copy URL", lambda: copy_text(url))]
            if it["cwd"]:
                items += [(None, None),
                          ("Open project in VS Code", lambda: run(["code", it["cwd"]])),
                          ("New terminal tab here", lambda: self.terminal.new_tab(it["cwd"]))]
            if it["pid"]:
                items += [(None, None), (f"Stop (PID {it['pid']})", lambda: (self._stop(it["pid"]),
                                                                           GLib.timeout_add(600, self.refresh)))]
            items += [(None, None), (f"Hide “{it['proc']}” from this bar", lambda: self.hide(it["proc"]))]
        else:
            name = it["name"]
            if ev.button == 1 and url:
                run(["xdg-open", url])
                return True
            items = []
            if url:
                items.append(("Open in browser", lambda: run(["xdg-open", url])))
            items += [("Logs in a terminal tab", lambda: self._in_tab(None, f"docker logs -f --tail 200 {shlex.quote(name)}")),
                      ("Shell in a terminal tab", lambda: self._in_tab(None, f"docker exec -it {shlex.quote(name)} sh")),
                      (None, None),
                      ("Restart", lambda: run(["docker", "restart", name])),
                      ("Stop" if it["running"] else "Start",
                       lambda: run(["docker", "stop" if it["running"] else "start", name])),
                      (None, None),
                      ("Copy name", lambda: copy_text(name))]
        popup_menu(ev, items)
        return True

    @staticmethod
    def _measure(cr, s, size, mono=False):
        lay = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family("Hack" if mono else FONT)
        fd.set_absolute_size(size * Pango.SCALE)
        lay.set_font_description(fd)
        lay.set_text(s, -1)
        return lay.get_pixel_size()[0]

    def _box(self, cr, x, w, key):
        hot = self.hover is not None and key is not None and self.hover[0] == key[0] and self.hover[1] is key[1]
        rrect(cr, x, 9, w, 32, 10)
        set_rgba(cr, C["sel"] if hot else C["ink"], 0.25 if hot else 0.05)
        cr.fill_preserve()
        set_rgba(cr, C["border"], 0.9)
        cr.set_line_width(1)
        cr.stroke()
        cr.new_path()
        if key:
            self.hits.append(((x, 9, w, 32), key))

    def _dot(self, cr, x, col, filled=True):
        cr.new_path()
        cr.arc(x, 25, 4, 0, 2 * math.pi)
        set_rgba(cr, col, 1 if filled else 0.9)
        if filled:
            cr.fill()
        else:
            cr.set_line_width(1.5)
            cr.stroke()

    def _chip(self, cr, x, key, dot, filled, head, tail, right_limit):
        """Status dot, mono `head` (port / name), dim `tail`. Returns its width, or 0 if it does not fit."""
        hw = self._measure(cr, head, 12.5, mono=True)
        tw = min(190, self._measure(cr, tail, 12)) if tail else 0
        w = 26 + hw + (tw + 8 if tail else 0) + 12
        if x + w > right_limit:
            return 0
        self._box(cr, x, w, key)
        self._dot(cr, x + 14, dot, filled)
        text(cr, head, x + 26, 30, 12.5, C["acc"], mono=True)
        if tail:
            text(cr, tail, x + 26 + hw + 8, 30, 12, C["dim"], maxw=190)
        return w

    def draw_modes(self, cr, Wf):
        """Overview | Coding | Focus, at the right end of the bar."""
        labels = [("overview", "Overview"), ("coding", "Coding"), ("focus", "Focus")]
        widths = [self._measure(cr, lb, 12.5) + 22 for _k, lb in labels]
        x0 = Wf - 14 - sum(widths) - 4
        rrect(cr, x0 - 4, 9, sum(widths) + 8, 32, 16)
        set_rgba(cr, C["ink"], 0.06)
        cr.fill()
        vline(cr, x0 - 14, 12, 38)
        x = x0
        for (key, lb), w in zip(labels, widths):
            on = self.mode == key
            hot = self.hover == ("mode", key)
            if on:
                rrect(cr, x, 12, w, 26, 13)
                set_rgba(cr, C["acc"], 1)
                cr.fill()
            elif hot:
                rrect(cr, x, 12, w, 26, 13)
                set_rgba(cr, C["ink"], 0.10)
                cr.fill()
            text(cr, lb, x + w / 2, 30, 12.5, C["on_acc"] if on else (C["text"] if hot else C["dim"]),
                 align="c", bold=on)
            self.hits.append(((x, 9, w, 32), ("mode", key)))
            x += w

    def draw_project(self, cr, x, limit):
        p = self.proj
        if not p:
            text(cr, "Open a project in VS Code to see it here", x, 31, 12.5, C["dim"])
            return x + 280
        # name, or a switcher when several projects are open
        others = [o for o in self.opened if o["path"] != p["path"]][:3]
        nw = min(200, self._measure(cr, p["name"], 13.5))
        w = 30 + nw + 12
        if others:
            rrect(cr, x, 9, w, 32, 10)
            set_rgba(cr, C["sel"], 0.28)
            cr.fill()
            self.hits.append(((x, 9, w, 32), ("proj", p)))
        else:
            self._box(cr, x, w, ("proj", p))
        glyph(cr, "folder", x + 10, 17, 16, C["acc"])
        text(cr, p["name"], x + 30, 30.5, 13.5, C["text"], bold=True, maxw=200)
        x += w + 6
        for o in others:
            ow = 14 + min(150, self._measure(cr, o["name"], 13)) + 14
            if x + ow > limit - 200:
                break
            self._box(cr, x, ow, ("pick", o["path"]))
            text(cr, o["name"], x + 14, 30, 13, C["dim"], maxw=150)
            x += ow + 6
        if others:
            x += 4
            vline(cr, x, 14, 36)
            x += 10
        # branch + ahead / behind
        if p["branch"]:
            sync = (f" ↑{p['ahead']}" if p["ahead"] else "") + (f" ↓{p['behind']}" if p["behind"] else "")
            label = p["branch"] + sync
            bw = min(180, self._measure(cr, label, 12.5, mono=True))
            w = 30 + bw + 12
            self._box(cr, x, w, ("branch", p))
            glyph(cr, "git-branch", x + 10, 17, 15, C["acc2"])
            text(cr, label, x + 30, 30, 12.5, C["text"], mono=True, maxw=180)
            x += w + 8
        # working tree
        label = f"±{p['dirty']} changed" if p["dirty"] else "clean"
        w = 26 + self._measure(cr, label, 12.5) + 12
        self._box(cr, x, w, ("dirty", p))
        self._dot(cr, x + 14, C["amber"] if p["dirty"] else C["mint"])
        text(cr, label, x + 26, 30, 12.5, C["text"])
        x += w + 8
        # pull request + CI
        pr = p.get("pr")
        if pr:
            label = f"#{pr['number']}"
            ci = {"ok": ("passing", C["mint"]), "fail": ("failing", C["red"]),
                  "pending": ("running", C["amber"])}.get(pr["ci"], ("open", C["dim"]))
            w = 30 + self._measure(cr, label, 12.5, mono=True) + 8 + self._measure(cr, ci[0], 12) + 12
            self._box(cr, x, w, ("pr", pr))
            glyph(cr, "pr", x + 10, 17, 15, ci[1])
            lw = text(cr, label, x + 30, 30, 12.5, C["acc"], mono=True)
            text(cr, ci[0], x + 30 + lw + 8, 30, 12, ci[1])
            x += w + 8
        # tests: one click runs them, the result stays on the chip
        if p.get("test"):
            tr = self.tests.get(p["path"], {})
            stt = tr.get("status")
            word = {"running": "testing…", "pass": f"tests passed · {since(tr.get('t', 0))}",
                    "fail": f"tests failed · {since(tr.get('t', 0))}"}.get(stt, "run tests")
            col = {"running": C["amber"], "pass": C["mint"], "fail": C["red"]}.get(stt, C["dim"])
            w = 26 + self._measure(cr, word, 12.5) + 12
            self._box(cr, x, w, ("test", p))
            self._dot(cr, x + 14, col, filled=stt is not None)
            text(cr, word, x + 26, 30, 12.5, C["text"] if stt else C["dim"])
            x += w + 8
        # last commit (takes what is left)
        if p.get("commit"):
            when = since(p["commit_t"])
            room = limit - x - 26 - self._measure(cr, when, 12) - 20
            if room > 80:
                cw = min(room, 300, self._measure(cr, p["commit"], 12.5))
                w = 14 + cw + 10 + self._measure(cr, when, 12) + 12
                self._box(cr, x, w, ("commit", p))
                text(cr, p["commit"], x + 14, 30, 12.5, C["text"], maxw=cw)
                text(cr, when, x + 14 + cw + 10, 30, 12, C["dim"])
                x += w + 8
        return x

    def on_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        self.hits = []
        Wf, H = self.W, self.H
        card_bg(cr, 0, 0, Wf, H, 16)
        self.draw_modes(cr, Wf)
        W = Wf - MODE_W                                  # everything else lives left of the mode switcher

        # the running section is sized first, from the right edge, so the project gets the rest
        servers = self.servers or []
        cont = self.containers
        run_w = 0
        for srv in servers[:5]:
            run_w += 26 + self._measure(cr, f":{srv['port']}", 12.5, mono=True) + 8 + \
                min(190, self._measure(cr, srv["label"], 12)) + 12 + 8
        if not any(s["kind"] == "server" for s in servers):
            run_w += 120
        if cont:
            run_w += 80 + sum(26 + self._measure(cr, c["name"], 12.5, mono=True) + 20 for c in cont[:3])
        run_w = min(run_w + 130, W * 0.55)
        split = W - run_w

        self.draw_project(cr, 14, split - 20)
        vline(cr, split, 12, H - 12)
        x = split + 14
        glyph(cr, "bolt", x, 15, 18, C["acc2"])
        text(cr, "Running", x + 24, 31, 13, C["text"], bold=True)
        x += 92
        if self.servers is None:
            text(cr, "looking…", x, 31, 12.5, C["dim"])
            return False
        limit = W - 14 - (0 if not cont else 80 + sum(26 + self._measure(cr, c["name"], 12.5, mono=True) + 20
                                                    for c in cont[:3]))
        if not any(s["kind"] == "server" for s in servers):
            x += text(cr, "no dev servers", x, 31, 12.5, C["dim"]) + 14
        hidden = 0
        for srv in servers:
            col = C["mint"] if srv["kind"] == "server" else C["dim"]
            w = self._chip(cr, x, ("srv", srv), col, True, f":{srv['port']}", srv["label"], limit)
            if not w:
                hidden += 1
                continue
            x += w + 8
        if hidden:
            x += text(cr, f"+{hidden}", x + 4, 31, 12.5, C["dim"]) + 12
        if cont is not None:
            x = max(x + 6, limit)
            text(cr, "Docker", x, 31, 13, C["text"], bold=True)
            x += 62
            if not cont:
                text(cr, "none", x, 31, 12.5, C["dim"])
            for c in cont:
                w = self._chip(cr, x, ("box", c), C["mint"] if c["running"] else C["dim"], c["running"],
                               c["name"], "", W - 14)
                if not w:
                    break
                x += w + 8
        return False


# ----------------------------------------------------------------- dock cards (extra widgets for big screens)
def term_run(terminal, path, cmd):
    """Open a terminal tab in `path` and run `cmd` there."""
    terminal.new_tab(path or HOME)
    GLib.timeout_add(400, lambda: (terminal.run_command(cmd), False)[1])


def draw_spark(cr, x, y, w, h, vals, color, vmax=100.0):
    if len(vals) < 2:
        return
    vmax = max(vmax, 1e-9)
    pts = [(x + w * i / (len(vals) - 1), y + h - h * max(0, min(vmax, v)) / vmax) for i, v in enumerate(vals)]
    cr.new_path()
    cr.move_to(*pts[0])
    for p in pts[1:]:
        cr.line_to(*p)
    cr.set_line_width(1.5)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    set_rgba(cr, color, 1)
    cr.stroke_preserve()
    cr.line_to(x + w, y + h)
    cr.line_to(x, y + h)
    cr.close_path()
    grad = cairo.LinearGradient(0, y, 0, y + h)
    r, g, b = rgb(color)
    grad.add_color_stop_rgba(0, r, g, b, 0.26)
    grad.add_color_stop_rgba(1, r, g, b, 0.0)
    cr.set_source(grad)
    cr.fill()


def fmt_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def prayer_ready():
    loc = CFG.get("location") or {}
    return bool((CFG.get("prayer") or {}).get("enabled")) and \
        isinstance(loc.get("latitude"), (int, float)) and isinstance(loc.get("longitude"), (int, float))


def prayer_state(now=None):
    """(times today, next prayer name, its datetime) from the configured location, or None."""
    if not prayer_ready():
        return None
    now = now or datetime.datetime.now()
    loc, pr = CFG["location"], CFG["prayer"]
    tz = time.localtime().tm_gmtoff / 3600

    def times(day):
        return PR.prayer_times(day.year, day.month, day.day, loc["latitude"], loc["longitude"], tz,
                               pr.get("method", "mwl"), pr.get("asr", "standard"))
    today = now.date()
    t = times(today)
    midnight = datetime.datetime.combine(today, datetime.time())
    for name in ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"):
        when = midnight + datetime.timedelta(hours=t[name])
        if when > now:
            return t, name, when
    tomorrow = today + datetime.timedelta(days=1)
    return t, "Fajr", datetime.datetime.combine(tomorrow, datetime.time()) + \
        datetime.timedelta(hours=times(tomorrow)["Fajr"])


PRAYER_AR = dict(Fajr="الفجر", Sunrise="الشروق", Dhuhr="الظهر", Asr="العصر", Maghrib="المغرب", Isha="العشاء")


def in_words(delta):
    mins = max(1, int(delta.total_seconds() // 60))
    return f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins} min"


class DockCard(Gtk.DrawingArea):
    """A 330 px tall card in the free space of a big screen. Subclasses draw body() and handle activate()."""
    KEY, TITLE, ICON = "", "", "folder"
    H = 330
    PREF_W = 500
    TICK = 2

    def __init__(self, terminal=None):
        super().__init__()
        self.terminal = terminal
        self.W = self.PREF_W
        self.set_size_request(self.W, self.H)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK |
                        Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.hits, self.hover = [], None
        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_press)
        self.connect("motion-notify-event", lambda _w, ev: self._set_hover(self._hit(ev.x, ev.y)))
        self.connect("leave-notify-event", lambda *_: self._set_hover(None))
        GLib.timeout_add_seconds(every(self.TICK), self._tick)

    def _tick(self):
        if self.get_mapped():
            self.queue_draw()
        return True

    def set_width(self, w):
        self.W = int(w)
        self.set_size_request(self.W, self.H)
        self.queue_resize()
        self.queue_draw()

    def set_height(self, h):
        self.H = int(h)
        self.set_size_request(self.W, self.H)
        self.queue_resize()
        self.queue_draw()

    def _on_draw(self, _w, cr):
        self._cr = cr
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        self.hits = []
        card_bg(cr, 0, 0, self.W, self.H, 20)
        glyph(cr, self.ICON, 20, 16, 22, C["acc2"])
        text(cr, self.TITLE, 52, 34, 16, C["text"], bold=True)
        try:
            self.body(cr, self.W, self.H)
        except Exception as exc:                       # a broken card must not take the others down
            print(f"kiln: {self.KEY} card: {exc!r}", flush=True)
        return False

    def body(self, cr, w, h):
        pass

    def region(self, key, x, y, w, h):
        self.hits.append(((x, y, w, h), key))
        if self.hover == key:
            cr = self._cr
            rrect(cr, x, y, w, h, 8)
            set_rgba(cr, C["ink"], 0.07)
            cr.fill()

    def _hit(self, px, py):
        for (x, y, w, h), key in reversed(self.hits):
            if x <= px < x + w and y <= py < y + h:
                return key
        return None

    def _set_hover(self, key):
        if key != self.hover:
            self.hover = key
            win = self.get_window()
            if win:
                win.set_cursor(Gdk.Cursor.new_from_name(Gdk.Display.get_default(), "pointer") if key else None)
            self.queue_draw()

    def _on_press(self, _w, ev):
        key = self._hit(ev.x, ev.y)
        if key is None:
            return False
        self.activate(key, ev)
        return True

    def activate(self, key, ev):
        pass


# --- Git: the current project's working tree and recent commits
class GitCard(DockCard):
    KEY, TITLE, ICON, PREF_W = "git", "Git", "git-branch", 620
    STATUS = {"M": "amber", "A": "mint", "D": "red", "R": "blue", "?": "dim", "U": "red"}

    def body(self, cr, w, h):
        proj = WORK.get("proj")
        if not proj:
            text(cr, "Open a project in VS Code or a terminal", 24, 98, 14, C["dim"])
            return
        pill(cr, w - 20, 33, proj["name"], 12, C["acc"], align="r")
        self.region(("branch",), 14, 52, w - 28, 34)
        glyph(cr, "git-branch", 22, 60, 16, C["acc2"])
        bw = text(cr, proj.get("branch") or "detached", 46, 76, 13.5, C["text"], mono=True, maxw=w / 2 - 70)
        x = 46 + bw + 12
        if proj.get("ahead"):
            x += pill(cr, x, 76, f"↑ {proj['ahead']}", 11.5, C["mint"]) + 6
        if proj.get("behind"):
            x += pill(cr, x, 76, f"↓ {proj['behind']}", 11.5, C["amber"]) + 6
        dirty = proj.get("dirty", 0)
        pill(cr, w - 22, 76, f"{dirty} changed" if dirty else "clean", 11.5, C["amber"] if dirty else C["mint"],
             align="r")
        hline(cr, 20, w - 20, 98)
        colw = (w - 40 - 24) / 2
        x0, x1 = 20, 20 + colw + 24
        text(cr, "CHANGES", x0, 122, 11, C["dim"], bold=True)
        text(cr, "RECENT COMMITS", x1, 122, 11, C["dim"], bold=True)
        files = proj.get("files") or []
        if not files:
            text(cr, "Working tree clean", x0, 152, 13, C["dim"])
        rows = max(4, (h - 150) // 24)
        for i, (code, path) in enumerate(files[:rows]):
            y = 148 + i * 24
            self.region(("file", path), x0 - 6, y - 16, colw + 12, 22)
            col = C[self.STATUS.get(code, "dim")]
            rrect(cr, x0, y - 13, 18, 18, 5)
            set_rgba(cr, col, 0.18)
            cr.fill()
            text(cr, code, x0 + 9, y, 11, col, bold=True, align="c")
            base, parent = os.path.basename(path), os.path.dirname(path)
            bwid = text(cr, base, x0 + 26, y, 12.5, C["text"], maxw=colw - 30)
            if parent and bwid < colw - 70:
                text(cr, parent, x0 + 26 + bwid + 8, y, 11, C["dim"], maxw=colw - 30 - bwid - 8)
        if len(files) > rows:
            text(cr, f"+{len(files) - rows} more", x0 + 26, h - 14, 11.5, C["dim"])
        log = proj.get("log") or []
        for i, (sha, ts, _author, subject) in enumerate(log[:rows]):
            y = 148 + i * 24
            self.region(("commit", sha), x1 - 6, y - 16, colw + 12, 22)
            text(cr, sha, x1, y, 11.5, C["acc"], mono=True)
            age = since(ts)
            aw = text(cr, age, x1 + colw, y, 11, C["dim"], align="r")
            text(cr, subject, x1 + 62, y, 12.5, C["text"], maxw=colw - 62 - aw - 8)

    def activate(self, key, ev):
        proj = WORK.get("proj")
        if not proj:
            return
        kind = key[0]
        if kind == "file":
            run(["code", "-g", os.path.join(proj["path"], key[1])])
        elif kind == "commit":
            if proj.get("gh"):
                open_url(f"{proj['gh']}/commit/{key[1]}")
            elif self.terminal:
                term_run(self.terminal, proj["path"], f"git show {key[1]}")
        elif kind == "branch" and self.terminal:
            term_run(self.terminal, proj["path"], "git status")


# --- System: CPU, memory, GPU, network, disk and the busiest programs
class SysSampler:
    N = 40

    def __init__(self):
        self.hist = {k: [0.0] * self.N for k in ("cpu", "ram", "gpu", "down")}
        self.now = dict(cpu=0.0, ram=0.0, ram_used=0, ram_total=0, gpu=None, gpu_temp=None, vram=None,
                        vram_total=None, down=0.0, up=0.0, disk=0.0, disk_free=0, cpu_temp=None,
                        batt=None, batt_state="", procs=[])
        self._cpu, self._net, self._n = None, None, 0
        self._temp_file = self._find_temp()
        self._ncpu = os.cpu_count() or 1
        import threading
        threading.Thread(target=self._run, daemon=True).start()

    @staticmethod
    def _find_temp():
        try:
            for hw in sorted(os.listdir("/sys/class/hwmon")):
                base = f"/sys/class/hwmon/{hw}"
                if open(f"{base}/name").read().strip() in ("coretemp", "k10temp", "zenpower", "cpu_thermal"):
                    return f"{base}/temp1_input"
        except OSError:
            pass
        return None

    def _run(self):
        while True:
            try:
                self._sample()
            except Exception as exc:
                print(f"kiln: sampler: {exc!r}", flush=True)
            time.sleep(2 * SLOW)

    def _push(self, key, value):
        h = self.hist[key]
        h.append(value)
        del h[0]

    def _sample(self):
        n = self.now
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:8]]
        idle, total = v[3] + v[4], sum(v)
        if self._cpu:
            di, dt = idle - self._cpu[0], total - self._cpu[1]
            n["cpu"] = 100.0 * (1 - di / dt) if dt > 0 else 0.0
        self._cpu = (idle, total)
        self._push("cpu", n["cpu"])
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, val = line.split(":")
                mem[k] = int(val.split()[0])
        n["ram_total"] = mem["MemTotal"] * 1024
        n["ram_used"] = (mem["MemTotal"] - mem["MemAvailable"]) * 1024
        n["ram"] = 100.0 * n["ram_used"] / n["ram_total"]
        self._push("ram", n["ram"])
        rx = tx = 0
        with open("/proc/net/dev") as f:
            for line in f.readlines()[2:]:
                name, rest = line.split(":", 1)
                if name.strip() == "lo" or name.strip().startswith(("veth", "docker", "br-", "virbr")):
                    continue
                cols = rest.split()
                rx, tx = rx + int(cols[0]), tx + int(cols[8])
        now = time.time()
        if self._net and now > self._net[2]:
            dt = now - self._net[2]
            n["down"], n["up"] = max(0, (rx - self._net[0]) / dt), max(0, (tx - self._net[1]) / dt)
        self._net = (rx, tx, now)
        self._push("down", n["down"])
        du = shutil.disk_usage("/")
        n["disk"], n["disk_free"] = 100.0 * du.used / du.total, du.free
        if self._temp_file:
            try:
                n["cpu_temp"] = int(open(self._temp_file).read()) / 1000
            except (OSError, ValueError):
                pass
        for b in ("BAT1", "BAT0"):
            try:
                n["batt"] = float(open(f"/sys/class/power_supply/{b}/capacity").read())
                n["batt_state"] = open(f"/sys/class/power_supply/{b}/status").read().strip()
                break
            except (OSError, ValueError):
                continue
        if self._n % 2 == 0 and shutil.which("nvidia-smi"):
            try:
                out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                                      "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                     timeout=3).stdout.strip().splitlines()
                u, t, mu, mt = [float(x) for x in out[0].split(",")]
                n["gpu"], n["gpu_temp"], n["vram"], n["vram_total"] = u, t, mu, mt
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                n["gpu"] = None
        if self._n % (6 if LIGHT else 2) == 0:         # `top` is the costly part of a sample
            n["procs"] = [tuple(p) for p in DEMO["top"]] if DEMO and DEMO.get("top") else self._top()
        if n["gpu"] is not None:
            self._push("gpu", n["gpu"])
        self._n += 1

    def _top(self):
        """The three busiest programs, as a share of the whole CPU (top's second sample is the live one)."""
        try:
            out = subprocess.run(["top", "-b", "-n", "2", "-d", "0.7", "-o", "%CPU", "-w", "160"],
                                 capture_output=True, text=True, timeout=6).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        block = out.split("\ntop - ")[-1].splitlines()
        usage = {}
        started = False
        for line in block:
            if line.strip().startswith("PID"):
                started = True
                continue
            if not started or not line.strip():
                continue
            cols = line.split(None, 11)
            if len(cols) < 12:
                continue
            try:
                usage[cols[11].strip()] = usage.get(cols[11].strip(), 0.0) + float(cols[8].replace(",", "."))
            except ValueError:
                continue
        ranked = sorted(usage.items(), key=lambda kv: -kv[1])[:3]
        return [(name, cpu / self._ncpu) for name, cpu in ranked]


_sampler = []


def sampler():
    if not _sampler:
        _sampler.append(SysSampler())
    return _sampler[0]


class SystemCard(DockCard):
    KEY, TITLE, ICON, PREF_W = "system", "System", "cpu", 520

    def __init__(self, terminal=None):
        super().__init__(terminal)
        self.s = sampler()

    def tile(self, cr, x, y, w, h, label, value, sub, hist=None, color=None, vmax=100.0):
        rrect(cr, x, y, w, h, 12)
        set_rgba(cr, C["ink"], 0.045)
        cr.fill_preserve()
        set_rgba(cr, C["border"], 0.5)
        cr.set_line_width(1)
        cr.stroke()
        color = color or C["acc"]
        text(cr, label, x + 14, y + 22, 10.5, C["dim"], bold=True)
        text(cr, value, x + 14, y + 52, 20, C["text"], bold=True, maxw=w - 28)
        text(cr, sub, x + 14, y + 71, 11, C["dim"], maxw=w - 28)
        if hist:
            draw_spark(cr, x + w * 0.52, y + 10, w * 0.44 - 6, h - 34, hist, color, vmax)

    def body(self, cr, w, h):
        n, hs = self.s.now, self.s.hist
        tw, th, gap = (w - 40 - 10) / 2, 82, 8
        x0, x1, y0 = 20, 20 + (w - 40 - 10) / 2 + 10, 58
        temp = f" · {n['cpu_temp']:.0f}°C" if n["cpu_temp"] else ""
        self.tile(cr, x0, y0, tw, th, "CPU", f"{n['cpu']:.0f}%", f"{os.cpu_count()} cores{temp}", hs["cpu"], C["acc"])
        self.tile(cr, x1, y0, tw, th, "MEMORY", f"{n['ram_used'] / 2 ** 30:.1f} GB",
                  f"of {n['ram_total'] / 2 ** 30:.0f} GB · {n['ram']:.0f}%", hs["ram"], C["acc2"])
        if n["gpu"] is None:
            self.tile(cr, x0, y0 + th + gap, tw, th, "GPU", "—", "no NVIDIA GPU found")
        else:
            gt = f" · {n['gpu_temp']:.0f}°C" if n["gpu_temp"] else ""
            vram = f"{n['vram'] / 1024:.1f}/{n['vram_total'] / 1024:.0f} GB" if n["vram"] is not None else ""
            self.tile(cr, x0, y0 + th + gap, tw, th, "GPU", f"{n['gpu']:.0f}%", f"{vram}{gt}", hs["gpu"], C["amber"])
        peak = max(max(hs["down"]), 100 * 1024)
        self.tile(cr, x1, y0 + th + gap, tw, th, "NETWORK", f"↓ {fmt_bytes(n['down'])}/s", f"↑ {fmt_bytes(n['up'])}/s",
                  hs["down"], C["mint"], peak)
        batt = ""
        if n["batt"] is not None:
            batt = f" · battery {n['batt']:.0f}%" + (" ⚡" if n["batt_state"] in ("Charging",) else "")
        self.tile(cr, x0, y0 + 2 * (th + gap), tw, th, "DISK", f"{n['disk']:.0f}%",
                  f"{fmt_bytes(n['disk_free'])} free{batt}", None)
        bar = n["disk"] / 100
        rrect(cr, x0 + tw * 0.52, y0 + 2 * (th + gap) + 30, tw * 0.44 - 6, 8, 4)
        set_rgba(cr, C["ink"], 0.10)
        cr.fill()
        rrect(cr, x0 + tw * 0.52, y0 + 2 * (th + gap) + 30, max(8, (tw * 0.44 - 6) * bar), 8, 4)
        set_rgba(cr, C["red"] if bar > 0.9 else (C["amber"] if bar > 0.75 else C["blue"]), 1)
        cr.fill()
        # the busiest programs
        yy = y0 + 2 * (th + gap)
        rrect(cr, x1, yy, tw, th, 12)
        set_rgba(cr, C["ink"], 0.045)
        cr.fill_preserve()
        set_rgba(cr, C["border"], 0.5)
        cr.set_line_width(1)
        cr.stroke()
        text(cr, "BUSIEST", x1 + 14, yy + 22, 10.5, C["dim"], bold=True)
        for i, (name, cpu) in enumerate(n["procs"][:3]):
            text(cr, name, x1 + 14, yy + 42 + i * 17, 12, C["text"], maxw=tw - 70)
            text(cr, f"{cpu:.0f}%", x1 + tw - 14, yy + 42 + i * 17, 12, C["acc"], bold=True, align="r")
        if not n["procs"]:
            text(cr, "measuring…", x1 + 14, yy + 46, 12, C["dim"])


# --- Running: dev servers, local services and Docker containers (same actions as the work bar)
class ServicesCard(DockCard):
    KEY, TITLE, ICON, PREF_W = "services", "Running", "bolt", 520

    def __init__(self, terminal=None, qbar=None):
        self.qbar = qbar
        super().__init__(terminal)

    def body(self, cr, w, h):
        dev = WORK.get("dev")
        if dev is None:
            text(cr, "looking for servers…", 24, 98, 13.5, C["dim"])
            return
        servers, containers = dev
        rows = [("srv", s) for s in servers]
        if containers is not None:
            rows.append(("hdr", "DOCKER"))
            rows += [("box", c) for c in containers]
        live = [s for s in servers if s["kind"] == "server"]
        if not live:
            text(cr, "No dev servers running", 24, 84, 13, C["dim"])
        y = 108 if not live else 84
        shown = 0
        for kind, it in rows:
            if y > h - 20:
                break
            if kind == "hdr":
                text(cr, it, 24, y, 10.5, C["dim"], bold=True)
                if not containers:
                    text(cr, "no containers", 24 + 70, y, 11.5, C["dim"])
                y += 22
                continue
            self.region((kind, it), 14, y - 17, w - 28, 26)
            if kind == "srv":
                col = C["mint"] if it["kind"] == "server" else C["dim"]
                cr.new_path()
                cr.arc(28, y - 5, 4, 0, 2 * math.pi)
                set_rgba(cr, col, 1)
                cr.fill()
                pw = text(cr, f":{it['port']}", 42, y, 12.5, C["acc"], mono=True)
                text(cr, it["label"], 42 + pw + 12, y, 12.5, C["text"], maxw=w - 42 - pw - 12 - 70)
                if it["kind"] == "server":
                    text(cr, "open ↗", w - 24, y, 11, C["dim"], align="r")
            else:
                cr.new_path()
                cr.arc(28, y - 5, 4, 0, 2 * math.pi)
                set_rgba(cr, C["mint"] if it["running"] else C["dim"], 1)
                if it["running"]:
                    cr.fill()
                else:
                    cr.set_line_width(1.5)
                    cr.stroke()
                text(cr, it["name"], 42, y, 12.5, C["text"], mono=True, maxw=w - 160)
                if it["ports"]:
                    text(cr, ", ".join(f":{p}" for p in it["ports"][:3]), w - 24, y, 11, C["dim"], align="r")
            y += 28
            shown += 1

    def activate(self, key, ev):
        if key[0] in ("srv", "box") and self.qbar:
            self.qbar.dev_action(ev, key)


# --- Tests: status of the last run and the tail of its output
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def log_tail(path, n=40):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    keep = [_ANSI.sub("", l).rstrip() for l in lines
            if "cannot set terminal process group" not in l and "no job control" not in l]
    return [l for l in keep if l.strip()][-n:]


class TestsCard(DockCard):
    KEY, TITLE, ICON, PREF_W = "tests", "Tests", "check-circle", 500
    TICK = 2

    def __init__(self, terminal=None, qbar=None):
        self.qbar = qbar
        super().__init__(terminal)

    def body(self, cr, w, h):
        proj = WORK.get("proj")
        if not proj:
            text(cr, "Open a project to see its tests", 24, 98, 14, C["dim"])
            return
        cmd = proj.get("test")
        if not cmd:
            cmds = project_commands(proj["path"])
            if not cmds:
                text(cr, f"No tests or scripts found in {proj['name']}", 24, 90, 14, C["text"], maxw=w - 48)
                text(cr, "A test script, manage.py, a Makefile, cargo, go or flutter makes this card useful.",
                     24, 114, 12, C["dim"], maxw=w - 48)
                return
            text(cr, "No tests found. The project's commands:", 24, 82, 13, C["dim"], maxw=w - 48)
            for i, (label, command) in enumerate(cmds[:max(2, (h - 120) // 30)]):
                y = 118 + i * 30
                self.region(("cmd", command), 14, y - 19, w - 28, 28)
                rrect(cr, 24, y - 13, 18, 18, 5)
                set_rgba(cr, C["acc"], 0.18)
                cr.fill()
                text(cr, "▶", 33, y, 9, C["acc"], align="c")
                text(cr, label, 52, y, 12.5, C["text"], mono=True, maxw=w - 80)
            return
        tr = (self.qbar.tests if self.qbar else {}).get(proj["path"], {})
        st = tr.get("status")
        word, col = {"running": ("Running…", C["amber"]), "pass": ("Passed", C["mint"]),
                     "fail": ("Failed", C["red"])}.get(st, ("Not run yet", C["dim"]))
        cr.new_path()
        cr.arc(30, 78, 6, 0, 2 * math.pi)
        set_rgba(cr, col, 1)
        cr.fill()
        text(cr, word, 46, 84, 19, C["text"], bold=True)
        sub = f"{since(tr['t'])} ago" + (f" · {tr['dur']:.1f}s" if tr.get("dur") else "") if tr.get("t") else \
            "one click runs them"
        text(cr, sub, w - 22, 80, 12, C["dim"], align="r")
        self.region(("run",), 14, 94, 118, 30)
        hot = self.hover == ("run",)
        rrect(cr, 20, 98, 106, 24, 12)
        set_rgba(cr, C["acc"], 0.9 if hot else 0.75)
        cr.fill()
        text(cr, "Run tests" if st != "running" else "Running…", 73, 115, 12, C["on_acc"], bold=True, align="c")
        text(cr, cmd, 142, 115, 11.5, C["dim"], mono=True, maxw=w - 142 - 22)
        hline(cr, 20, w - 20, 134)
        lines = log_tail(tr.get("log", "")) if tr.get("log") else []
        if not lines:
            text(cr, "No output yet", 24, 160, 12.5, C["dim"])
            return
        room = max(3, int((h - 144 - 12) // 16))
        shown = lines[-room:]
        self.region(("log",), 14, 140, w - 28, h - 150)
        for i, line in enumerate(shown):
            low = line.lower()
            bad = any(k in low for k in ("fail", "error", "traceback", "assert", "✗", "not ok"))
            good = any(k in low for k in ("passed", " ok", "all good", "✓", "success"))
            text(cr, line, 24, 158 + i * 16, 11.5, C["red"] if bad else (C["mint"] if good else C["dim"]),
                 mono=True, maxw=w - 48)

    def activate(self, key, ev):
        proj = WORK.get("proj")
        if not proj or not self.qbar:
            return
        if key[0] == "cmd":
            if self.terminal:
                term_run(self.terminal, proj["path"], key[1])
        elif key == ("run",):
            self.qbar.run_tests(proj["path"])
        elif key == ("log",):
            log = self.qbar.tests.get(proj["path"], {}).get("log")
            if log and self.terminal:
                term_run(self.terminal, proj["path"], f"less -R +G {shlex.quote(log)}")


# --- Today: a month calendar, the Hijri date and prayer times
class DayCard(DockCard):
    KEY, TITLE, ICON, PREF_W = "day", "Today", "calendar", 640
    TICK = 20

    @staticmethod
    def specs():
        return (560, 640) if prayer_ready() else (330, 360)

    def body(self, cr, w, h):
        import calendar as cal
        today = datetime.date.today()
        arabic = (CFG.get("prayer") or {}).get("language", "en") == "ar"
        text(cr, PR.hijri_text(today.year, today.month, today.day, arabic) + " ≈", w - 22, 33, 12.5, C["dim"],
             align="r", maxw=w - 130, ltr=True)
        calw = 300 if prayer_ready() else w - 40
        first = {"mon": 0, "sat": 5, "sun": 6}.get(str(CFG.get("week_start", "sun")).lower(), 6)
        text(cr, today.strftime("%B %Y"), 20, 78, 14, C["text"], bold=True)
        cell = calw / 7
        names = ["M", "T", "W", "T", "F", "S", "S"]
        order = [(first + i) % 7 for i in range(7)]
        for i, d in enumerate(order):
            text(cr, names[d], 20 + cell * i + cell / 2, 106, 11, C["dim"], bold=True, align="c")
        weeks = cal.Calendar(first).monthdayscalendar(today.year, today.month)
        for r, week in enumerate(weeks):
            for c, day in enumerate(week):
                if not day:
                    continue
                cx, cy = 20 + cell * c + cell / 2, 134 + r * 30
                if day == today.day:
                    cr.new_path()
                    cr.arc(cx, cy - 5, 13, 0, 2 * math.pi)
                    set_rgba(cr, C["acc"], 1)
                    cr.fill()
                    text(cr, str(day), cx, cy, 12.5, C["on_acc"], bold=True, align="c")
                else:
                    weekend = order[c] in (4, 5)
                    text(cr, str(day), cx, cy, 12.5, C["dim"] if weekend else C["text"], align="c")
        if not prayer_ready():
            return
        vline(cr, 340, 58, h - 22)
        x0 = 362
        city = (CFG.get("location") or {}).get("city") or "Prayer times"
        text(cr, city.upper(), x0, 78, 11, C["dim"], bold=True, maxw=w - x0 - 20)
        now = datetime.datetime.now()
        t_today, nxt, nxt_dt = prayer_state(now)
        lang = (CFG.get("prayer") or {}).get("language", "en")
        label = PRAYER_AR if lang == "ar" else {}
        rows = ["Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha"]
        for i, name in enumerate(rows):
            y = 108 + i * 34
            hh = t_today[name]
            when = datetime.datetime.combine(today, datetime.time()) + datetime.timedelta(hours=hh)
            is_next = name == nxt
            passed = when <= now and not is_next
            if is_next:
                rrect(cr, x0 - 10, y - 20, w - x0 - 10, 30, 10)
                set_rgba(cr, C["acc"], 0.16)
                cr.fill()
            col = C["acc"] if is_next else (C["dim"] if passed or name == "Sunrise" else C["text"])
            text(cr, label.get(name, name), x0, y, 13.5 if name != "Sunrise" else 12, col, bold=is_next)
            text(cr, when.strftime(HMP), w - 24 - (74 if is_next else 0), y, 13.5, col, bold=is_next, align="r")
            if is_next:
                text(cr, in_words(nxt_dt - now), w - 24, y, 11.5, C["acc"], align="r")


# --- Notes: a scratchpad per project (and a global one)
NOTES_DIR = f"{APP}/notes"


class NotesCard(Gtk.Box):
    KEY, TITLE, PREF_W, H, HEAD = "notes", "Notes", 430, 330, 50

    def __init__(self, terminal=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.W = self.PREF_W
        self.target = "project"
        self.cur_file, self._loading, self._save_id = None, False, 0
        self.hits, self.hover = [], None
        self.head = Gtk.DrawingArea()
        self.head.set_size_request(120, self.HEAD)
        self.head.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK |
                             Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.head.connect("draw", self._draw_head)
        self.head.connect("button-press-event", self._press)
        self.head.connect("motion-notify-event", lambda _w, ev: self._hover(self._hit(ev.x, ev.y)))
        self.head.connect("leave-notify-event", lambda *_: self._hover(None))
        self.view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.view.get_style_context().add_class("kiln-notes")
        self.view.set_left_margin(20)
        self.view.set_right_margin(16)
        self.view.set_top_margin(4)
        self.view.set_bottom_margin(14)
        self.buf = self.view.get_buffer()
        self.buf.connect("changed", self._changed)
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sc.get_style_context().add_class("kiln-notes")
        sc.add(self.view)
        self.hint = Gtk.Label(xalign=0, yalign=0)
        self.hint.get_style_context().add_class("kiln-hint")
        self.hint.set_margin_start(20)
        self.hint.set_margin_top(5)
        self.hint.set_halign(Gtk.Align.START)
        self.hint.set_valign(Gtk.Align.START)
        over = Gtk.Overlay()
        over.add(sc)
        over.add_overlay(self.hint)
        over.set_overlay_pass_through(self.hint, True)
        self.pack_start(self.head, False, False, 0)
        self.pack_start(over, True, True, 0)
        self.set_size_request(self.W, self.H)
        self._load()
        GLib.timeout_add_seconds(every(2), self._follow_project)

    def set_width(self, w):
        self.W = int(w)
        self.set_size_request(self.W, self.H)
        self.queue_resize()

    def set_height(self, h):
        self.H = int(h)
        self.set_size_request(self.W, self.H)
        self.queue_resize()

    # --- which file
    def _project(self):
        proj = WORK.get("proj")
        return proj["name"] if proj else None

    def _path(self):
        if self.target == "project" and self._project():
            name = re.sub(r"[^A-Za-z0-9._-]", "_", self._project())
            return f"{NOTES_DIR}/{name}.md"
        return f"{NOTES_DIR}/_scratch.md"

    def _load(self):
        self._flush()
        self.cur_file = self._path()
        self._loading = True
        try:
            with open(self.cur_file, encoding="utf-8") as f:
                self.buf.set_text(f.read())
        except OSError:
            self.buf.set_text("")
        self._loading = False
        self._update_hint()
        self.head.queue_draw()

    def _follow_project(self):
        if self._path() != self.cur_file:
            self._load()
        return True

    def _update_hint(self):
        name = self._project() if self.target == "project" else None
        self.hint.set_text(f"Notes for {name}…" if name else "Scratch notes…")
        self.hint.set_visible(self.buf.get_char_count() == 0)

    def _changed(self, *_):
        self._update_hint()
        if self._loading:
            return
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(700, self._flush)

    def _flush(self):
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = 0
        if self.cur_file and not self._loading:
            body = self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), True)
            try:
                if body.strip() or os.path.exists(self.cur_file):
                    os.makedirs(NOTES_DIR, exist_ok=True)
                    with open(self.cur_file, "w", encoding="utf-8") as f:
                        f.write(body)
            except OSError:
                pass
        return False

    # --- header
    def _hit(self, px, py):
        for (x, y, w, h), key in reversed(self.hits):
            if x <= px < x + w and y <= py < y + h:
                return key
        return None

    def _hover(self, key):
        if key != self.hover:
            self.hover = key
            self.head.queue_draw()

    def _press(self, _w, ev):
        key = self._hit(ev.x, ev.y)
        if key and key[0] == "tab" and key[1] != self.target:
            self.target = key[1]
            self._load()
        return True

    def _draw_head(self, _w, cr):
        self.hits = []
        rrect(cr, 22, 18, 17, 20, 4)                       # a small notepad
        set_rgba(cr, C["acc2"], 1)
        cr.set_line_width(1.8)
        cr.stroke()
        for ly in (25, 30, 35):
            cr.move_to(26, ly)
            cr.line_to(35, ly)
        cr.set_line_width(1.4)
        cr.stroke()
        x = 52
        tabs = [("project", self._project() or "Project"), ("scratch", "Scratch")]
        for key, label in tabs:
            on = self.target == key
            hot = self.hover == ("tab", key)
            tw = text(cr, label, x, 34, 15 if on else 13, C["text"] if on or hot else C["dim"], bold=on, maxw=150)
            self.hits.append(((x - 6, 12, tw + 12, 30), ("tab", key)))
            x += tw + 18
        return False


# --- where the cards go: row 1 runs to the right edge, row 2 stops at the Focus card
DOCK_ORDER = ["git", "system", "day", "notes", "tests", "services"]
DOCK_MIN = {"git": 460, "system": 420, "services": 380, "day": 560, "notes": 320, "tests": 380}
DOCK_PREF = {"git": 620, "system": 520, "services": 520, "day": 640, "notes": 430, "tests": 500}
DOCK_ACTIVE = set()              # the cards actually placed on this screen (the clock card looks at it)


def plan_dock(L, wanted, specs):
    """[(key, x, y, width)] for as many of the wanted cards as fit in the free space of the layout L."""
    ex = L["extra"]
    gap = ex["gap"]
    x0 = L["term"][0] + ex["term0"][0] + gap
    if L["focus"][1] - L["term"][1] >= DockCard.H + gap:
        rows = [(L["term"][1], ex["W"] - ex["right"] - x0), (L["focus"][1], L["focus"][0] - gap - x0)]
    else:
        rows = [(L["term"][1], L["focus"][0] - gap - x0)]
    out, rest = [], [k for k in wanted if k in specs]
    for y, free in rows:
        row, used = [], 0
        while rest:
            need = specs[rest[0]][0] + (gap if row else 0)
            if used + need > free:
                break
            used += need
            row.append(rest.pop(0))
        if not row:
            continue
        room = free - gap * (len(row) - 1)
        prefs = [specs[k][1] for k in row]
        widths = [max(specs[k][0], int(p * room / sum(prefs))) for k, p in zip(row, prefs)]
        widths[-1] = max(specs[row[-1]][0], room - sum(widths[:-1]))
        x = x0
        for k, wd in zip(row, widths):
            out.append((k, x, y, wd))
            x += wd + gap
    return out


# ----------------------------------------------------------------- prayer popup (click the clock card)
PRAYER_ORDER = ("Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha")
_prayer_win = []


class PrayerPopup(Gtk.Window):
    W, ROW = 380, 34

    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("Kiln Prayer")
        self.get_style_context().add_class("kiln")
        self.set_app_paintable(True)
        vis = self.get_screen().get_rgba_visual()
        if vis:
            self.set_visual(vis)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.area = Gtk.DrawingArea()
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK |
                             Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.add(self.area)
        self.hits, self.hover, self.state, self._opened = [], None, None, 0
        self.area.connect("draw", self.on_draw)
        self.area.connect("button-press-event", self.on_press)
        self.area.connect("motion-notify-event", self.on_motion)
        self.area.connect("leave-notify-event", lambda *_: self.set_hover(None))
        self.connect("key-press-event", lambda _w, ev: (self.hide(), True)[-1] if ev.keyval == Gdk.KEY_Escape else False)
        # clicking anywhere else closes it (the first moments are ignored: the window is still being focused)
        self.connect("focus-out-event", lambda *_: (self.hide() if time.time() - self._opened > 0.5 else None, False)[-1])
        self.connect("draw", lambda _w, cr: (cr.set_operator(cairo.OPERATOR_CLEAR), cr.paint(),
                                               cr.set_operator(cairo.OPERATOR_OVER), False)[-1])

    def popup(self, px, py):
        self.state = prayer_state()
        h = 128 + (len(PRAYER_ORDER) * self.ROW + 74 if self.state else 118)
        self.H = h
        self.area.set_size_request(self.W, h)
        self.resize(self.W, h)
        scr = Gdk.Display.get_default().get_monitor_at_point(int(px), int(py)).get_geometry()
        self.move(int(min(max(scr.x + 8, px - 30), scr.x + scr.width - self.W - 8)),
                  int(min(max(scr.y + 30, py + 14), scr.y + scr.height - h - 8)))
        self._opened = time.time()
        self.show_all()
        self.present()
        self.area.queue_draw()

    def set_hover(self, key):
        if key != self.hover:
            self.hover = key
            self.area.queue_draw()

    def on_motion(self, _w, ev):
        self.set_hover(next((k for (x, y, w, h), k in reversed(self.hits) if x <= ev.x < x + w and y <= ev.y < y + h), None))

    def on_press(self, _w, ev):
        key = next((k for (x, y, w, h), k in reversed(self.hits) if x <= ev.x < x + w and y <= ev.y < y + h), None)
        if key == ("settings",):
            run(["/usr/bin/python3", f"{SHARE}/settings.py", "lock"])
            self.hide()
        return True

    def on_draw(self, _w, cr):
        self.hits = []
        W, H = self.W, self.H
        card_bg(cr, 0, 0, W, H, 20, alpha=0.98)
        today = datetime.date.today()
        pr = CFG.get("prayer") or {}
        glyph(cr, "moon", 22, 20, 18, C["acc2"])
        text(cr, time.strftime("%A, %-d %B %Y"), 50, 36, 15, C["text"], bold=True, maxw=W - 70)
        text(cr, PR.hijri_text(today.year, today.month, today.day, True), 50, 60, 14, C["dim"], maxw=W - 70, ltr=True)
        hline(cr, 20, W - 20, 78)
        if not self.state:
            text(cr, "Prayer times are off", 24, 112, 14, C["text"])
            text(cr, "Set your city and turn them on in Settings.", 24, 134, 12, C["dim"], maxw=W - 48)
            self.settings_button(cr, 24, H - 54, W - 48, "Open prayer settings")
            return False
        times, nxt, when = self.state
        loc = CFG.get("location") or {}
        text(cr, (loc.get("city") or "Your location") + " · " + dict(PR.METHOD_NAMES).get(pr.get("method", "mwl"), "").strip(),
             24, 100, 12, C["dim"], maxw=W - 48)
        midnight = datetime.datetime.combine(today, datetime.time())
        now = datetime.datetime.now()
        for i, name in enumerate(PRAYER_ORDER):
            y = 114 + i * self.ROW
            at = midnight + datetime.timedelta(hours=times[name])
            is_next = name == nxt and name != "Sunrise"
            passed = at < now and not is_next
            if is_next:
                rrect(cr, 14, y, W - 28, self.ROW - 4, 10)
                set_rgba(cr, C["acc"], 0.16)
                cr.fill()
            col = C["dim"] if passed or name == "Sunrise" else C["text"]
            cy = y + 22
            text(cr, PRAYER_AR[name], 26, cy, 15, C["acc"] if is_next else col, bold=is_next, ltr=True, maxw=110)
            text(cr, name, 130, cy, 12.5, C["dim"])
            text(cr, at.strftime(HMP), W - 26, cy, 15, C["acc"] if is_next else col, bold=is_next, align="r")
            if is_next:
                text(cr, "in " + in_words(when - now), W - 120, cy, 11.5, C["dim"], align="r")
        self.settings_button(cr, 24, H - 54, W - 48, "Prayer settings")
        return False

    def settings_button(self, cr, x, y, w, label):
        hot = self.hover == ("settings",)
        self.hits.append(((x, y, w, 34), ("settings",)))
        rrect(cr, x, y, w, 34, 11)
        set_rgba(cr, C["ink"], 0.10 if hot else 0.05)
        cr.fill_preserve()
        set_rgba(cr, C["border"], 0.9)
        cr.set_line_width(1)
        cr.stroke()
        text(cr, label, x + w / 2, y + 22, 13, C["acc"] if hot else C["text"], align="c")


def show_prayer_popup(px, py):
    if not _prayer_win:
        _prayer_win.append(PrayerPopup())
    win = _prayer_win[0]
    if win.get_visible():
        win.hide()
    else:
        win.popup(px, py)


# ----------------------------------------------------------------- windows
def _css_rgba(hexs, a):
    r, g, b = (int(hexs[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{a})"


CSS_TEMPLATE = """
window.kiln { background: transparent; }
entry.kiln-entry { background: #$menu; color: #$text; border: 1px solid #$acc; border-radius: 12px; padding: 4px 12px; caret-color: #$acc; font-size: 14px; }
entry.kiln-entry:focus { border-color: #$acc; box-shadow: none; }
window.kiln-launcher { background: transparent; }
entry.kiln-search { background: $ink06; color: #$text; border: 1px solid $border45; border-radius: 14px; padding: 8px 14px; min-height: 30px; font-size: 17px; caret-color: #$acc; }
entry.kiln-search:focus { border-color: #$acc; box-shadow: none; }
list.kiln-results, list.kiln-results row { background: transparent; }
list.kiln-results row { border-radius: 12px; padding: 7px 10px; margin: 2px 0; color: #$text; }
list.kiln-results row:selected { background: $sel35; }
label.kiln-title { color: #$text; font-size: 14.5px; }
label.kiln-sub { color: #$dim; font-size: 12px; }
label.kiln-chip { color: #$acc; font-size: 11px; }
menu, .menu, .context-menu { background-color: $menu97; color: #$menu_fg; border: 1px solid $border50; border-radius: 12px; padding: 6px; }
menu menuitem, .menu menuitem { padding: 7px 14px; border-radius: 8px; color: #$menu_fg; }
menu menuitem:hover, .menu menuitem:hover { background-color: $sel85; color: #$on_sel; }
menu menuitem:disabled, .menu menuitem:disabled { color: #$dim; }
menu separator, .menu separator { background-color: $ink10; margin: 4px 6px; min-height: 1px; }
menu arrow { color: #$dim; }
tooltip, tooltip.background { background-color: $menu97; color: #$menu_fg; border: 1px solid $border50; border-radius: 8px; }
textview.kiln-notes, textview.kiln-notes text { background-color: transparent; color: #$text; caret-color: #$acc; font-family: "$font"; font-size: 13.5px; }
label.kiln-hint { color: #$dim; opacity: 0.65; font-family: "$font"; font-size: 13.5px; }
textview.kiln-notes text selection { background-color: $sel35; color: #$text; }
scrolledwindow.kiln-notes, scrolledwindow.kiln-notes viewport { background: transparent; }
scrolledwindow.kiln-notes scrollbar slider { min-width: 4px; background-color: $ink10; border-radius: 2px; }
dialog, dialog .dialog-vbox, dialog > box, colorchooser { background-color: #$menu; color: #$menu_fg; }
dialog headerbar, dialog .titlebar { background: #$menu; color: #$menu_fg; border-bottom: 1px solid $border40; box-shadow: none; }
dialog entry { background: $ink06; color: #$text; border: 1px solid $border60; border-radius: 10px; caret-color: #$acc; }
dialog entry selection { background-color: #$sel; color: #$on_sel; }
dialog button { background: $ink06; color: #$menu_fg; border: 1px solid $border50; border-radius: 10px; box-shadow: none; }
dialog button:hover { background: $ink10; }
dialog button.default, dialog button.suggested-action { background: #$sel; border-color: #$sel; color: #$on_sel; }
"""


def build_css():
    from string import Template
    v = dict(C)
    v.update(font=FONT, ink06=_css_rgba(C["ink"], 0.06), ink10=_css_rgba(C["ink"], 0.10),
             border40=_css_rgba(C["border"], 0.40), border45=_css_rgba(C["border"], 0.45),
             border50=_css_rgba(C["border"], 0.50), border60=_css_rgba(C["border"], 0.60),
             sel35=_css_rgba(C["sel"], 0.35), sel85=_css_rgba(C["sel"], 0.85), menu97=_css_rgba(C["menu"], 0.97))
    return Template(CSS_TEMPLATE).substitute(v).encode()


CSS = build_css()


def make_window(title, child, x, y, w, h, focus=False, resizable=False):
    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title(title)
    win.get_style_context().add_class("kiln")
    win.set_app_paintable(True)
    vis = win.get_screen().get_rgba_visual()
    if vis:
        win.set_visual(vis)
    win.set_decorated(False)
    win.set_resizable(resizable)
    win.set_default_size(w, h)
    win.set_accept_focus(True)
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
    win.stick()
    win.add(child)
    win.move(x, y)
    win.connect("draw", lambda wd, cr: (cr.set_operator(cairo.OPERATOR_CLEAR), cr.paint(),
                                          cr.set_operator(cairo.OPERATOR_OVER), False)[-1])
    win._pos = (x, y)                # modes move windows; a re-shown window goes to its current spot
    win.connect("map-event", lambda wd, *_: (wd.move(*wd._pos), wd.stick(), False)[-1])
    return win


def compute_layout(g, wa):
    """Where every widget goes on the primary monitor `g` (wa = its work area without the
    top bar and dock). Matches the original 1920x1080 layout and grows on bigger screens:
    the terminal gets taller and wider (up to a comfortable size), the icon grid gets more
    columns and rows, the work bar spans the whole width."""
    global COLS, ROWS, ICONS_W, ICONS_H, ICONS_X, ICONS_Y
    gap, right, bottom = 18, 28, 26
    left = max(100, wa.x - g.x + 44)                # clear of the dock
    top = max(66, wa.y - g.y + 26)                  # clear of the top bar
    W, H = g.width, g.height
    bar_y = H - bottom - WorkBar.H
    row2_top = top + CARDS_H + gap
    term_h = max(TerminalCard.H, min(760, bar_y - gap - row2_top))
    term_y = row2_top                               # directly under the cards, like on a small screen
    side_w = FocusCard.W + gap + NowPlaying.W
    term_w = max(TerminalCard.W, min(1500, W - left - right - side_w - gap))   # wider is not better
    music_x = W - right - NowPlaying.W
    focus_x = music_x - gap - FocusCard.W
    side_y = bar_y - gap - FocusCard.H              # focus + music sit on the work bar
    ICONS_X, ICONS_Y = left + CARDS_W + 32, 40
    COLS = max(6, (W - right - ICONS_X) // CELL_W)
    ROWS = max(4, min(8, (term_y - gap - ICONS_Y) // CELL_H))     # the grid stays above the terminal
    ICONS_W, ICONS_H = COLS * CELL_W, ROWS * CELL_H
    TerminalCard.W, TerminalCard.H = term_w, term_h
    WorkBar.W = W - left - right
    return dict(cards=(left, top), term=(left, term_y), focus=(focus_x, side_y), music=(music_x, side_y),
                bar=(left, bar_y), icons=(ICONS_X, ICONS_Y),
                extra=dict(gap=gap, right=right, bar_y=bar_y, top=top, left=left, W=W, H=H, term0=(term_w, term_h)))


def main():
    GLib.set_prgname("kiln")
    # single instance via pidfile
    os.makedirs(CACHE, exist_ok=True)
    with open(f"{CACHE}/app.pid", "w") as f:
        f.write(str(os.getpid()))

    # hang watchdog: if the GTK main loop stalls for 4 s, every thread's stack goes to the
    # journal (journalctl --user -u kiln); `kill -USR2 <pid>` dumps it on demand
    import faulthandler
    faulthandler.register(signal.SIGUSR2, all_threads=True)
    GLib.timeout_add(int(1000 * SLOW), lambda: (faulthandler.dump_traceback_later(int(4 * SLOW)), True)[1])

    if Gdk.Screen.get_default() is None:
        # no display (e.g. the session just ended): exit cleanly so systemd does not loop
        print("kiln: no display, not starting", flush=True)
        return 0
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    disp = Gdk.Display.get_default()
    mon = disp.get_primary_monitor() or disp.get_monitor(0)
    g = mon.get_geometry()
    L = compute_layout(g, mon.get_workarea())

    term = TerminalCard()
    cards = Cards(term)
    focus = FocusCard()

    w1 = make_window("Kiln Cards", cards, g.x + L["cards"][0], g.y + L["cards"][1], CARDS_W, CARDS_H)
    # terminal window paints its own rounded card behind the (transparent) VTE
    w2 = make_window("Kiln Terminal", term, g.x + L["term"][0], g.y + L["term"][1], TerminalCard.W, TerminalCard.H,
                     resizable=True)

    def paint_term_bg(_w, cr):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        card_bg(cr, 0, 0, term.W, term.H, 20, term.alpha)
        return False

    # make_window already connected a clearing handler; ours runs after and paints the card
    w2.connect("draw", paint_term_bg)
    w3 = make_window("Kiln Focus", focus, g.x + L["focus"][0], g.y + L["focus"][1], FocusCard.W, FocusCard.H)
    music = NowPlaying()
    w5 = make_window("Kiln Music", music, g.x + L["music"][0], g.y + L["music"][1], NowPlaying.W, NowPlaying.H)
    qbar = WorkBar(term)
    w6 = make_window("Kiln Workbar", qbar, g.x + L["bar"][0], g.y + L["bar"][1], WorkBar.W, WorkBar.H)

    # dock cards: whatever fits in the free space of a big screen (nothing on a small one)
    wanted = [k for k in DOCK_ORDER if k in ((CFG.get("dock") or {}).get("cards") or [])]
    specs = {k: (DOCK_MIN[k], DOCK_PREF[k]) for k in DOCK_ORDER}
    specs["day"] = DayCard.specs()
    makers = {"git": lambda: GitCard(term), "system": lambda: SystemCard(term),
              "services": lambda: ServicesCard(term, qbar), "day": lambda: DayCard(term),
              "notes": lambda: NotesCard(term), "tests": lambda: TestsCard(term, qbar)}
    dock, dock_geo, dock_card = {}, {}, {}
    for key, dx, dy, dw in plan_dock(L, wanted, specs):
        card = makers[key]()
        card.set_width(dw)
        dwin = make_window(f"Kiln {key.title()}", card, g.x + dx, g.y + dy, dw, DockCard.H, resizable=True)
        if key == "notes":                    # a plain box: its window paints the card behind it
            dwin.connect("draw", lambda _w, cr, c=card: (card_bg(cr, 0, 0, c.W, c.H, 20), False)[-1])
        dock[key] = dwin
        dock_geo[key] = (dx, dy, dw)
        dock_card[key] = card
    DOCK_ACTIVE.update(dock)
    icons = IconsGrid(term)
    w4 = make_window("Kiln Icons", icons, g.x + L["icons"][0], g.y + L["icons"][1], ICONS_W, ICONS_H)
    launcher = Launcher(term, icons)

    def ctrl_k(_w, ev):
        if ev.keyval in (Gdk.KEY_k, Gdk.KEY_K) and ev.state & Gdk.ModifierType.CONTROL_MASK:
            launcher.toggle()
            return True
        return False

    for w in (w1, w3, w4, w5, w6):  # not the terminal card: Ctrl+K is "kill line" there
        w.connect("key-press-event", ctrl_k)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR1, lambda: (launcher.toggle(), True)[1])

    # ---- modes: Overview (everything), Coding (terminal beside the cards), Focus (terminal only)
    wins = dict(cards=w1, term=w2, focus=w3, music=w5, bar=w6, icons=w4)
    wins.update({f"dock_{k}": w for k, w in dock.items()})

    # Focus mode: a veil that dims the wallpaper, and the Pomodoro timer as a pill in the terminal header
    veil_area = Gtk.DrawingArea()
    veil_area.set_size_request(g.width, g.height)
    veil_area.connect("draw", lambda _w, cr: (cr.set_source_rgba(0, 0, 0, 0.55), cr.paint(), False)[-1])
    veil_win = make_window("Kiln Veil", veil_area, g.x, g.y, g.width, g.height)
    veil_win.set_accept_focus(False)

    def hud():
        left = max(0.0, focus.end_at - time.time()) if focus.running else focus.left
        m, sec = divmod(int(left), 60)
        return f"{'●' if focus.running else '▶'}  {m}:{sec:02d}", focus.running
    GLib.timeout_add_seconds(1, lambda: (term.head.queue_draw() if term.hud else None, True)[1])
    ex = L["extra"]
    state = {"mode": "overview"}

    def set_banners(on):
        """Focus mode silences notification banners; the previous setting is restored afterwards."""
        saved = f"{CACHE}/banners-saved"
        schema = ["org.gnome.desktop.notifications", "show-banners"]
        if not on:
            if os.path.exists(saved):
                prev = open(saved, encoding="utf-8").read().strip() or "true"
                run(["gsettings", "set", *schema, prev])
                os.remove(saved)
            return
        if not (CFG.get("modes") or {}).get("focus_mutes_notifications", True) or os.path.exists(saved):
            return
        try:
            cur = subprocess.run(["gsettings", "get", *schema], capture_output=True, text=True, timeout=3).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return
        with open(saved, "w", encoding="utf-8") as f:
            f.write(cur)
        run(["gsettings", "set", *schema, "false"])

    def place(win, x, y, w=None, h=None):
        win._pos = (g.x + x, g.y + y)
        win.move(*win._pos)
        if w:
            win.resize(w, h)

    def apply_mode(name, remember=True):
        name = name if name in MODES else "overview"
        state["mode"] = name
        top, gap, bar_y, left = ex["top"], ex["gap"], ex["bar_y"], ex["left"]
        side = [k for k in ("git", "tests", "notes") if k in dock]       # the project's own cards
        arrange = {}                                                     # dock card -> (x, y, w, h)
        veil = False
        term.alpha, pt = 0.80, 11
        if name == "coding":
            tx = left + CARDS_W + gap
            avail = ex["W"] - tx - ex["right"]
            tw, th = min(avail, 2000), bar_y - gap - top
            ty, show = top, {"cards", "term", "bar"}
            if side and avail >= 1500:                                   # Git, Tests and Notes beside the terminal
                tw = max(1100, int(avail * 0.60))
                cx, cw = tx + tw + gap, avail - tw - gap
                ch = (th - gap * (len(side) - 1)) // len(side)
                for i, key in enumerate(side):
                    arrange[key] = (cx, top + i * (ch + gap), cw, ch)
                show |= {f"dock_{k}" for k in side}
        elif name == "focus":
            tw, th = min(1700, int(ex["W"] * 0.62)), min(bar_y - gap - top, 940)
            tx, ty = (ex["W"] - tw) // 2, top + (bar_y - gap - top - th) // 2
            show, veil, term.alpha, pt = {"term", "bar"}, True, 0.97, 13
        else:
            (tx, ty), (tw, th) = L["term"], ex["term0"]
            show = set(wins)
        term.set_size(tw, th)
        term.set_font_pt(pt)
        place(w2, tx, ty, tw, th)
        w2.set_visible("term" in show)
        w2.queue_draw()
        for key, dwin in dock.items():                                   # cards go back to their own spots
            card = dock_card[key]
            ox, oy, ow = dock_geo[key]
            x, y, w_, h_ = arrange.get(key, (ox, oy, ow, DockCard.H))
            card.set_width(w_)
            card.set_height(h_)
            place(dwin, x, y, w_, h_)
        for key, win in wins.items():
            if key != "term":
                win.set_visible(key in show)
        veil_win.set_visible(veil)
        if veil:                                                         # dim the wallpaper, keep the terminal on top
            veil_win.get_window().lower()
            w2.get_window().raise_()
            w6.get_window().raise_()
        term.hud, term.on_hud = (hud, focus.toggle) if name == "focus" else (None, None)
        term.head.queue_draw()
        qbar.mode = name
        qbar.queue_draw()
        set_banners(name == "focus")
        if remember:
            try:
                with open(f"{CACHE}/mode", "w", encoding="utf-8") as f:
                    f.write(name)
            except OSError:
                pass
        if name != "overview":
            term.term.grab_focus()

    qbar.on_mode = apply_mode

    def on_request(*_):
        try:
            with open(CMD_FILE, encoding="utf-8") as f:
                lines = f.read().splitlines()
            os.remove(CMD_FILE)
        except OSError:
            return False
        for line in lines:
            words = line.split()
            if not words:
                continue
            if words[0] == "mode":
                want = words[1] if len(words) > 1 else "next"
                if want == "next":
                    want = MODES[(MODES.index(state["mode"]) + 1) % len(MODES)]
                apply_mode(want)
            elif words[0] == "test" and qbar.proj:
                qbar.run_tests(qbar.proj["path"])
        return False
    try:
        cmd_mon = Gio.File.new_for_path(CMD_FILE).monitor_file(Gio.FileMonitorFlags.NONE, None)
        cmd_mon.connect("changed", lambda *_: GLib.timeout_add(150, on_request))
    except GLib.Error:
        pass

    def on_stop(signame):
        set_banners(False)
        print(f"kiln: received {signame}, exiting", flush=True)
        Gtk.main_quit()
        return False
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, on_stop, sig.name)
    for w in (w1, w2, w3, w4, w5, w6, *dock.values()):
        w.show_all()
    cards.entry.hide()
    try:
        first = open(f"{CACHE}/mode", encoding="utf-8").read().strip()
    except OSError:
        first = "overview"
    set_banners(False) if first != "focus" else None       # recover from a crash while in Focus
    apply_mode(first, remember=False)

    def relayout_if_needed():
        m = disp.get_primary_monitor() or disp.get_monitor(0)
        ng = m.get_geometry()
        if (ng.x, ng.y, ng.width, ng.height) != (g.x, g.y, g.width, g.height):
            print(f"kiln: screen is now {ng.width}x{ng.height}, re-laying out", flush=True)
            # restart from outside this unit (start.sh stops the unit it would be running in)
            run(["systemd-run", "--user", "--collect", "--quiet", f"{SHARE}/start.sh"])
        return False

    def on_monitors(*_):
        if getattr(main, "_relayout", 0):
            GLib.source_remove(main._relayout)
        main._relayout = GLib.timeout_add(2500, relayout_if_needed)   # let the screens settle
    Gdk.Screen.get_default().connect("monitors-changed", on_monitors)
    Gdk.Screen.get_default().connect("size-changed", on_monitors)

    # data refresh schedule
    refresh("projects")
    refresh("github")
    refresh("accounts")
    GLib.timeout_add_seconds(every(120), lambda: (refresh("projects"), True)[1])
    GLib.timeout_add_seconds(every(300), lambda: (refresh("github"), True)[1])
    GLib.timeout_add_seconds(every(120), lambda: (refresh("accounts"), True)[1])

    def tick():
        cards.metrics.sample()
        cards.push_history()
        cards.poll_jobs()
        for p in list(_procs):
            if p.poll() is not None:
                _procs.remove(p)
        cards.area.queue_draw()
        qbar.queue_draw()
        return True

    enforce_dock_position()
    schedule_update_checks()
    cards.metrics.sample()
    GLib.timeout_add_seconds(every(2), tick)
    GLib.timeout_add_seconds(every(60), lambda: (icons.queue_draw(), True)[1])      # keeps the "recent" dots fresh
    Gtk.main()


if __name__ == "__main__":
    sys.exit(main())
