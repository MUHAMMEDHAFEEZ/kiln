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
FONT = "Ubuntu Sans"

# ---- settings (~/.config/kiln/config.json, documented in docs/CONFIGURATION.md)
DEFAULT_CONFIG = {
    "desktop_name": "{Host}'s Desktop",
    "clock": "12h",
    "desktop_dir": "",
    "project_dirs": ["~/Desktop"],
    "web_search": {"name": "DuckDuckGo", "url": "https://duckduckgo.com/?q={q}"},
    "integrations": {"github": True, "lark": False, "claude_code": True},
    "now_playing": {"youtube_music_only": False},
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


def text(cr, s, x, y, size, color, bold=False, align="l", maxw=None, alpha=1.0, strike=False, mono=False):
    """Draw text with its baseline at y. Returns the drawn width."""
    lay = PangoCairo.create_layout(cr)
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


def open_url(url):
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


WORK = dict(proj=None)      # the project the bottom bar is showing (set by WorkBar)


# ----------------------------------------------------------------- work data (GitHub json, Lark)
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


LARK_DONE_FILE = f"{CACHE}/lark-done.json"
LARK_BIN = f"{HOME}/.local/bin/lark-cli"


def lark_state():
    """('ok' | 'setup' | 'login' | 'missing' | 'pending' | 'off', hint)"""
    if not INTEGRATIONS.get("lark"):
        return "off", ""
    err = load_json(f"{CACHE}/lark-error.json")
    if isinstance(err, dict):
        e = err.get("error") or {}
        if e.get("type") == "missing":
            return "missing", "lark-cli is not installed"
        if e.get("subtype") == "not_configured" or e.get("type") == "config":
            return "setup", "lark-cli config init --new --brand lark"
        return "login", "lark-cli auth login --domain task,calendar"
    if load_json(f"{CACHE}/lark-tasks.json") is None:
        return "pending", ""
    return "ok", ""


def load_lark_done():
    d = load_json(LARK_DONE_FILE) or {}
    if d.get("date") != time.strftime("%Y-%m-%d"):
        return {}
    return d.get("done", {})


def save_lark_done(done):
    try:
        with open(LARK_DONE_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": time.strftime("%Y-%m-%d"), "done": done}, f)
    except OSError:
        pass


def load_lark_tasks():
    out, seen = [], set()
    if not INTEGRATIONS.get("lark"):
        return out
    for d in _walk(load_json(f"{CACHE}/lark-tasks.json")):
        guid = d.get("guid") or d.get("task_guid")
        title = d.get("summary") or d.get("title")
        if not guid or not isinstance(title, str) or guid in seen:
            continue
        if _ts(d.get("completed_at")):
            continue
        seen.add(guid)
        url = d.get("url") or d.get("applink") or d.get("app_link") or ""
        out.append(dict(key="lark:" + guid, guid=guid, text=title.strip() or "(untitled)",
                        due=_ts(d.get("due") or d.get("due_time") or d.get("due_at")),
                        url=url if isinstance(url, str) else "", source="lark", done=False))
    return out


def load_lark_events():
    out, seen = [], set()
    for d in _walk(load_json(f"{CACHE}/lark-agenda.json")):
        title = d.get("summary") or d.get("title")
        start = d.get("start_time") or d.get("start")
        if not isinstance(title, str) or start is None:
            continue
        s = _ts(start)
        if not s or d.get("status") == "cancelled":
            continue
        e = _ts(d.get("end_time") or d.get("end")) or s + 1800
        eid = d.get("event_id") or (title, s)
        if eid in seen:
            continue
        seen.add(eid)
        vchat = d.get("vchat") if isinstance(d.get("vchat"), dict) else {}
        url = vchat.get("meeting_url") or d.get("meeting_url") or d.get("app_link") or d.get("applink") or ""
        out.append(dict(title=title.strip() or "(no title)", start=s, end=e, url=url if isinstance(url, str) else ""))
    out.sort(key=lambda ev: ev["start"])
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


def due_label(ts):
    """(text, colour) for a due timestamp."""
    lt = time.localtime(ts)
    d = datetime.date(lt.tm_year, lt.tm_mon, lt.tm_mday)
    today = datetime.date.today()
    hm = "" if (lt.tm_hour, lt.tm_min) in ((0, 0), (23, 59)) else time.strftime(" " + HMP, lt)
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
        self.lark_done = load_lark_done()
        self.today_tab = "today"           # "today" (personal + Lark) or "project" (WORK["proj"])
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
                self.lark_done.pop(guid, None)
                save_lark_done(self.lark_done)
            refresh("lark")

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
    def complete_lark(self, it):
        if it["guid"] in self.lark_done:
            return
        self.lark_done[it["guid"]] = it["text"]
        save_lark_done(self.lark_done)
        try:
            p = subprocess.Popen([LARK_BIN, "task", "+complete", "--as", "user", "--task-id", it["guid"]],
                                 env=clean_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.jobs.append((p, it["guid"], it["text"]))
        except OSError:
            self.lark_done.pop(it["guid"], None)

    def toggle_item(self, key):
        it = self.items.get(key)
        if not it:
            return
        if it["source"] == "lark":
            if not it["done"]:
                self.complete_lark(it)
        elif it["source"] == "project":
            todo_toggle(it["idx"], it["file"])
        elif it["source"] == "local":
            todo_toggle(it["idx"])

    def lark_setup_cmd(self):
        st, hint = lark_state()
        if st == "setup":
            return "lark-cli config init --new --brand lark"
        return "lark-cli auth login --domain task,calendar"

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
            run(["gnome-system-monitor"])
        elif kind == "task_toggle":
            self.toggle_item(key[1])
        elif kind == "task_row":
            it = self.items.get(key[1])
            if it and it["source"] == "lark" and it.get("url"):
                open_url(it["url"])
        elif kind == "add":
            self.show_entry()
        elif kind == "tab":
            self.today_tab, self.todo_off = key[1], 0
        elif kind == "lark_connect":
            self.terminal.run_command(self.lark_setup_cmd())
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
            refresh("lark")
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
            elif it["source"] == "lark":
                items = [("Open in Lark", lambda: open_url(it["url"]))] if it.get("url") else []
                if not it["done"]:
                    items.append(("Mark complete in Lark", lambda: (self.complete_lark(it), self.area.queue_draw())))
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
        self.region(cr, (x + 156, y + 8, w - 164, h - 16), ("strip",), r=12)
        tw = text(cr, time.strftime(HM), x + 20, y + 54, 40, C["text"])
        if not H24:
            text(cr, time.strftime("%p"), x + 26 + tw, y + 54, 14, C["dim"], bold=True)
        text(cr, time.strftime("%a, %b %d"), x + 22, y + 78, 13, C["dim"])
        vline(cr, x + 150, y + 18, y + h - 18)
        rows = [("CPU", self.metrics.cpu, self.hist["cpu"], C["acc"]),
                ("RAM", self.metrics.mem(), self.hist["ram"], C["acc2"])]
        for i, (label, val, hist, col) in enumerate(rows):
            cy = y + 36 + i * 32
            text(cr, label, x + 168, cy, 11, C["dim"])
            text(cr, f"{val:.0f}%", x + 238, cy, 14, C["text"], bold=True, align="r")
            self.sparkline(cr, x + 252, cy - 16, w - 252 - 20, 18, hist, col)

    # 2) today: Lark tasks + local tasks
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
            if it["source"] == "lark":
                right -= pill(cr, right, cy + 4, "Lark", 10.5, tone("5b8cff"), align="r") + 6
            elif claude:
                right -= pill(cr, right, cy + 4, "now" if it.get("running") else "Claude", 10.5,
                              C["amber"] if it.get("running") else C["acc2"], align="r") + 6
            if it.get("due") and not it["done"]:
                lbl, col = due_label(it["due"])
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
        lark = load_lark_tasks()
        open_guids = {t["guid"] for t in lark}
        for t in lark:
            if t["guid"] in self.lark_done:
                t["done"] = True
        lark_done_gone = [dict(key=f"lark:{g}", guid=g, text=txt, done=True, due=None, source="lark", url="")
                          for g, txt in self.lark_done.items() if g not in open_guids]
        allitems = lark + local + lark_done_gone
        opened = sorted([t for t in allitems if not t["done"]], key=lambda t: (t["due"] is None, t["due"] or 0))
        done = [t for t in allitems if t["done"]]
        items = opened + done
        self.items = {t["key"]: t for t in items}
        self._n_todo = len(items)

        self.today_tabs(cr, x, y)
        total = len(items)
        text(cr, f"{len(done)} of {total} done" if total else "no tasks", x + w - 22, y + 33, 12.5, C["dim"], align="r")
        ay = self.add_button(cr, x, y, w, h)
        n_lark = len([t for t in items if t["source"] == "lark" and not t["done"]])
        if n_lark:
            text(cr, f"{n_lark} from Lark", x + w - 22, ay, 11.5, C["dim"], align="r")
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

    # 3) agenda (Lark calendar)
    def card_agenda(self, cr):
        x, y, w, h = self.card(cr, "agenda")
        self.header(cr, x, y, "calendar", C["blue"], "Agenda")
        st, hint = lark_state()
        if st != "ok":
            if st == "off":
                text(cr, "No calendar connected", x + 22, y + 76, 13.5, C["text"])
                text(cr, "Turn on the Lark integration in", x + 22, y + 100, 12.5, C["dim"])
                text(cr, "~/.config/kiln/config.json", x + 22, y + 120, 12.5, C["dim"], mono=True)
                return
            msg = {"missing": "Install lark-cli to see meetings",
                   "setup": "Connect Lark to see tasks & meetings",
                   "login": "Sign in to Lark to see tasks & meetings",
                   "pending": "Loading Lark…"}[st]
            text(cr, msg, x + 22, y + 76, 13.5, C["text"])
            if st in ("setup", "login"):
                self.region(cr, (x + 14, y + 92, w - 28, 40), ("lark_connect",), r=12)
                rrect(cr, x + 20, y + 98, w - 40, 30, 10)
                set_rgba(cr, C["acc"], 1 if self.hover == ("lark_connect",) else 0.9)
                cr.fill()
                cr.set_line_width(1.6)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                set_rgba(cr, C["on_acc"], 1)
                cr.move_to(x + 31, y + 107)
                cr.line_to(x + 36, y + 113)
                cr.line_to(x + 31, y + 119)
                cr.move_to(x + 39, y + 119)
                cr.line_to(x + 46, y + 119)
                cr.stroke()
                text(cr, self.lark_setup_cmd(), x + 54, y + 118, 11.5, C["on_acc"], maxw=w - 80, mono=True)
                text(cr, "click to run it in the terminal below", x + 22, y + 150, 11.5, C["dim"])
            return
        now = time.time()
        events = [e for e in load_lark_events() if e["end"] > now]
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
            text(cr, "scanning repositories…", x + 24, y + 86, 13, C["dim"])
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
        self.hover, self.hits, self.fhits = None, [], []
        self.branch = ""
        if not os.path.exists(QUICK):
            write_lines(QUICK, ["# one command per line (shown as buttons under the terminal)",
                                "git status", "git pull", "npm run dev", "docker ps", "code ."])
        self.new_tab(os.path.expanduser("~/Desktop"))
        GLib.timeout_add_seconds(1, self.poll_cwd)

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
        t.set_font(Pango.FontDescription("Hack 11"))
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

    def new_tab(self, path=None):
        path = path or os.path.expanduser("~/Desktop")
        t = self.make_term()
        tab = dict(term=t, pid=None, cwd=path, start=path)
        t.connect("child-exited", lambda _t, _st, tab=tab: GLib.timeout_add(200, self.on_exit, tab))
        self.tabs.append(tab)
        self.stack.add_named(t, str(id(t)))
        t.show_all()
        self.spawn(tab)
        self.switch(len(self.tabs) - 1)
        return tab

    def spawn(self, tab):
        env = [f"{k}={v}" for k, v in clean_env().items()] + ["COLORTERM=truecolor"]
        tab["term"].spawn_async(Vte.PtyFlags.DEFAULT, tab["start"],
                                ["/bin/bash", "--rcfile", f"{CACHE}/term.bashrc"], env,
                                GLib.SpawnFlags.DEFAULT, None, None, -1, None,
                                lambda _t, pid, err, *_a, tab=tab: tab.__setitem__("pid", pid if not err else None))

    def on_exit(self, tab):
        if tab not in self.tabs:
            return False
        if len(self.tabs) == 1:
            tab["term"].reset(True, True)
            tab["start"] = tab["cwd"] if os.path.isdir(tab["cwd"]) else os.path.expanduser("~/Desktop")
            self.spawn(tab)
        else:
            self.close_tab(self.tabs.index(tab), kill=False)
        return False

    def close_tab(self, i, kill=True):
        if not (0 <= i < len(self.tabs)):
            return
        tab = self.tabs.pop(i)
        if kill and tab["pid"]:
            try:
                os.kill(tab["pid"], signal.SIGHUP)
            except OSError:
                pass
        self.stack.remove(tab["term"])
        if not self.tabs:
            self.new_tab()
            return
        if i < self.active:
            self.active -= 1
        elif i == self.active:
            self.active = max(0, i - 1)
        self.switch(min(self.active, len(self.tabs) - 1))

    def switch(self, i):
        self.active = i
        t = self.tabs[i]["term"]
        self.stack.set_visible_child(t)
        t.grab_focus()
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
            self.close_tab(self.active)
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
        elif kind == "plus":
            self.new_tab(self.tabs[self.active]["cwd"])
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
        return [l.strip() for l in read_lines(QUICK) if l.strip() and not l.lstrip().startswith("#")][:8]

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
        for cmd in self.chips():
            cr.save()
            lay = PangoCairo.create_layout(cr)
            fd = Pango.FontDescription("Hack")
            fd.set_absolute_size(11.5 * Pango.SCALE)
            lay.set_font_description(fd)
            lay.set_text(cmd, -1)
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
        GLib.timeout_add_seconds(2, self._poll_files)

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


def _git(path, *args, timeout=3):
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
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


def scan_project(path, with_pr):
    if not path:
        return None
    info = dict(path=path, name=os.path.basename(path), branch=_git(path, "rev-parse", "--abbrev-ref", "HEAD"))
    st = _git(path, "status", "--porcelain", timeout=6)
    info["dirty"] = len(st.splitlines()) if st else 0
    ab = _git(path, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    info["behind"], info["ahead"] = (int(x) for x in ab.split()) if ab and len(ab.split()) == 2 else (0, 0)
    lc = _git(path, "log", "-1", "--format=%ct%x09%s")
    if lc and "\t" in lc:
        t, msg = lc.split("\t", 1)
        info["commit"], info["commit_t"] = msg, int(t)
    info["gh"] = github_url(path)
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
        self.refresh()
        GLib.timeout_add_seconds(4, self.refresh)

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
        W, H = self.W, self.H
        card_bg(cr, 0, 0, W, H, 16)

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
    v.update(ink06=_css_rgba(C["ink"], 0.06), ink10=_css_rgba(C["ink"], 0.10),
             border40=_css_rgba(C["border"], 0.40), border45=_css_rgba(C["border"], 0.45),
             border50=_css_rgba(C["border"], 0.50), border60=_css_rgba(C["border"], 0.60),
             sel35=_css_rgba(C["sel"], 0.35), sel85=_css_rgba(C["sel"], 0.85), menu97=_css_rgba(C["menu"], 0.97))
    return Template(CSS_TEMPLATE).substitute(v).encode()


CSS = build_css()


def make_window(title, child, x, y, w, h, focus=False):
    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title(title)
    win.get_style_context().add_class("kiln")
    win.set_app_paintable(True)
    vis = win.get_screen().get_rgba_visual()
    if vis:
        win.set_visual(vis)
    win.set_decorated(False)
    win.set_resizable(False)
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
    win.connect("map-event", lambda wd, *_: (wd.move(x, y), wd.stick(), False)[-1])
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
    term_h = max(TerminalCard.H, min(620, bar_y - gap - row2_top))
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
                bar=(left, bar_y), icons=(ICONS_X, ICONS_Y))


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
    GLib.timeout_add(1000, lambda: (faulthandler.dump_traceback_later(4), True)[1])

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
    w2 = make_window("Kiln Terminal", term, g.x + L["term"][0], g.y + L["term"][1], TerminalCard.W, TerminalCard.H)

    def paint_term_bg(_w, cr):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        card_bg(cr, 0, 0, TerminalCard.W, TerminalCard.H, 20, 0.80)
        return False

    # make_window already connected a clearing handler; ours runs after and paints the card
    w2.connect("draw", paint_term_bg)
    w3 = make_window("Kiln Focus", focus, g.x + L["focus"][0], g.y + L["focus"][1], FocusCard.W, FocusCard.H)
    music = NowPlaying()
    w5 = make_window("Kiln Music", music, g.x + L["music"][0], g.y + L["music"][1], NowPlaying.W, NowPlaying.H)
    qbar = WorkBar(term)
    w6 = make_window("Kiln Workbar", qbar, g.x + L["bar"][0], g.y + L["bar"][1], WorkBar.W, WorkBar.H)
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

    def on_stop(signame):
        print(f"kiln: received {signame}, exiting", flush=True)
        Gtk.main_quit()
        return False
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, on_stop, sig.name)
    for w in (w1, w2, w3, w4, w5, w6):
        w.show_all()
    cards.entry.hide()

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
    refresh("lark")
    GLib.timeout_add_seconds(120, lambda: (refresh("projects"), True)[1])
    GLib.timeout_add_seconds(300, lambda: (refresh("github"), True)[1])
    GLib.timeout_add_seconds(120, lambda: (refresh("lark"), True)[1])

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

    cards.metrics.sample()
    GLib.timeout_add_seconds(2, tick)
    Gtk.main()


if __name__ == "__main__":
    sys.exit(main())
