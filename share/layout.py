#!/usr/bin/python3
"""The views of the desktop (the list in the work bar: Overview | Coding | Focus | Tasks | Books) and where the widgets sit
in the ones you can arrange.

    ~/.config/kiln/layout.json
    {"version": 3,
     "custom": {"v1": "Design"},                          views you made (each is an Overview of its own)
     "hidden": ["focus"],                                 views that are out of the list (kept, not deleted)
     "order": ["overview", "v1", "coding", ...],          the order of the list (you drag the views)
     "places": {"overview": {"card_today": {"x": 120, "y": 80}}, "coding": {}, "v1": {...}}}

    layout.py list           the views in the list, in order
    layout.py hidden         the views you hid

Overview, Coding and the views you make have a free arrangement of widgets. Focus, Tasks and Books keep Kiln's own. Any view can
be hidden from the list and shown again; the ones you made can also be renamed and deleted; Overview, Coding, Focus, Tasks and
Books cannot be deleted. Positions are relative to the top-left corner of the screen. A widget with no entry stays where Kiln
puts it. `hidden` takes a widget off a view, `added` puts one there that is not part of it by default. A file from an earlier
Kiln (one set of places, or named layouts) is converted: the layouts that were not active become views of their own.
No GTK in here, so it can be tested without a display."""
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
CONF = os.environ.get("KILN_CONFIG", f"{HOME}/.config/kiln")
FILE = f"{CONF}/layout.json"
BUILTIN = {"overview": "Overview", "coding": "Coding", "focus": "Focus", "tasks": "Tasks", "books": "Books"}
FREE_BUILTIN = ("overview", "coding")                      # the built-in views whose widgets you arrange
MODES = FREE_BUILTIN
SNAP = 8                                                   # positions and sizes snap to this many pixels while dragging
MIN_W, MIN_H = 160, 90
GRID_MAX = 20000                                           # a number from a hand-edited file is never trusted beyond this
MAX_VIEWS, MAX_NAME = 16, 24


def snap(v, step=SNAP):
    return int(round(v / step) * step)


def _num(v, default=None):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return int(v) if v == v and abs(v) <= GRID_MAX else default


def clean_name(name):
    """A view name: printable, one line, at most 24 characters (it goes in a button), not empty. Raises ValueError."""
    name = " ".join(str(name or "").split())[:MAX_NAME].strip()
    if not name or not name.isprintable():
        raise ValueError("a view needs a name")
    return name


class Layout:
    """The views and the saved places of the widgets. Call save() after changes. `mode` is the view being looked at."""

    def __init__(self, path=None, keys=None, prefixes=()):
        self.path = path or FILE
        self.keys = set(keys) if keys is not None else None        # the widgets that exist (others in the file are dropped)
        self.prefixes = tuple(prefixes)                              # ...and the kinds of key that may come and go (custom_)
        self.places = {m: {} for m in FREE_BUILTIN}
        self.custom = {}                                             # id -> name of the views you made (in order)
        self.hidden_views = set()
        self.sequence = []                                           # the order of every view in the work bar
        self.mode = "overview"
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        if isinstance(raw.get("places"), dict):                     # version 3
            self._load_v3(raw)
        elif isinstance(raw.get("layouts"), dict):                  # version 2: named layouts
            self._load_v2(raw)
        elif isinstance(raw.get("overview"), dict):                 # version 1: one set of places
            self.places["overview"] = self._clean(raw["overview"])
        self._normalize(raw.get("order") if isinstance(raw.get("order"), list) else [])

    # ---- reading the file
    def _clean(self, entries):
        out = {}
        for key, e in (entries.items() if isinstance(entries, dict) else []):
            if not isinstance(e, dict) or (self.keys is not None and key not in self.keys
                                           and not key.startswith(self.prefixes or ("\0",))):
                continue
            clean = {}
            for f in ("x", "y", "w", "h"):
                n = _num(e.get(f))
                if n is not None and (f in "xy" or n >= (MIN_W if f == "w" else MIN_H)):
                    clean[f] = n
            for f in ("hidden", "added"):
                if e.get(f) is True:
                    clean[f] = True
            if clean:
                out[key] = clean
        return out

    def _load_v3(self, raw):
        custom = raw.get("custom")
        for vid, name in (custom.items() if isinstance(custom, dict) else []):
            try:
                name = clean_name(name)
            except ValueError:
                continue
            if re.fullmatch(r"v\d{1,4}", str(vid)) and len(self.custom) < MAX_VIEWS and not self._taken(name):
                self.custom[vid] = name
        for view in list(FREE_BUILTIN) + list(self.custom):
            self.places[view] = self._clean(raw["places"].get(view))
        hid = raw.get("hidden")
        self.hidden_views = {v for v in (hid if isinstance(hid, list) else []) if self.exists(v)}
        if len(self.hidden_views) >= len(BUILTIN) + len(self.custom):          # never hide them all
            self.hidden_views = set()

    def _load_v2(self, raw):
        """Named layouts of an earlier Kiln: the active one is Overview and Coding, each other one becomes a view."""
        layouts = {n: m for n, m in raw["layouts"].items() if isinstance(m, dict)}
        active = raw.get("active") if raw.get("active") in layouts else next(iter(layouts), None)
        if active is None:
            return
        for m in FREE_BUILTIN:
            self.places[m] = self._clean(layouts[active].get(m))
        hid = raw.get("hidden") if isinstance(raw.get("hidden"), list) else []
        for name, modes in layouts.items():
            if name == active or len(self.custom) >= MAX_VIEWS:
                continue
            try:
                label = clean_name(name)
            except ValueError:
                continue
            n = 2
            while self._taken(label):
                label = clean_name(f"{name[:MAX_NAME - 3]} {n}")
                n += 1
            vid = self._new_id()
            self.custom[vid] = label
            self.places[vid] = self._clean(modes.get("overview"))
            if name in hid:
                self.hidden_views.add(vid)

    # ---- the views
    def _normalize(self, wanted):
        """The saved order, without views that are gone or repeated; a view that is not in it goes where it belongs by
        default (yours after Coding, the others where they always were)."""
        seq = []
        for v in wanted:
            if self.exists(v) and v not in seq:
                seq.append(v)
        default = ["overview", "coding"] + list(self.custom) + ["focus", "tasks", "books"]
        for i, v in enumerate(default):
            if v not in seq:
                prev = next((p for p in reversed(default[:i]) if p in seq), None)
                seq.insert(seq.index(prev) + 1 if prev else 0, v)
        self.sequence = seq

    def exists(self, view):
        return view in BUILTIN or view in self.custom

    def free(self, view):
        """Can the widgets of this view be arranged?"""
        return view in FREE_BUILTIN or view in self.custom

    def title(self, view):
        return BUILTIN.get(view) or self.custom.get(view, view)

    def order(self, tasks=True):
        """Every view in the order of the work bar (Overview, Coding, yours, Focus, Tasks, Books until you drag them)."""
        return [v for v in self.sequence if tasks or v != "tasks"]

    def visible_views(self, tasks=True):
        return [v for v in self.order(tasks) if v not in self.hidden_views]

    def hidden_list(self, tasks=True):
        return [v for v in self.order(tasks) if v in self.hidden_views]

    def find(self, name):
        """The id of a view from its id or its name (any case), or None."""
        name = str(name or "").strip()
        low = name.lower()
        for v in self.order():
            if v == name or self.title(v).lower() == low:
                return v
        return None

    def _taken(self, name):
        low = name.lower()
        return any(low == t.lower() for t in list(BUILTIN.values()) + list(self.custom.values()))

    def _new_id(self):
        n = 1
        while f"v{n}" in self.custom:
            n += 1
        return f"v{n}"

    def create_view(self, name, copy_from="overview"):
        """A new view that starts as a copy of another one's places (a new Overview). Returns its id."""
        name = clean_name(name)
        if self._taken(name):
            raise ValueError(f"there is already a view called {name}")
        if len(self.custom) >= MAX_VIEWS:
            raise ValueError(f"{MAX_VIEWS} views of your own is the most")
        vid = self._new_id()
        src = self.places.get(copy_from, {})
        self.custom[vid] = name
        self.places[vid] = {k: dict(v) for k, v in src.items()}
        older = [v for v in list(self.custom)[:-1] if v in self.sequence]
        anchor = older[-1] if older else "coding"
        self.sequence.insert(self.sequence.index(anchor) + 1 if anchor in self.sequence else len(self.sequence), vid)
        return vid

    def rename_view(self, view, name):
        if view not in self.custom:
            raise ValueError("only the views you made can be renamed")
        name = clean_name(name)
        if self._taken(name) and name.lower() != self.custom[view].lower():
            raise ValueError(f"there is already a view called {name}")
        self.custom[view] = name
        return name

    def delete_view(self, view):
        if view not in self.custom:
            raise ValueError("only the views you made can be deleted (hide the others)")
        del self.custom[view]
        self.places.pop(view, None)
        self.hidden_views.discard(view)
        self.sequence = [v for v in self.sequence if v != view]

    def hide_view(self, view, tasks=True):
        """Take a view out of the list (it is kept). At least one view stays in the list."""
        if not self.exists(view):
            raise ValueError("no such view")
        if [v for v in self.visible_views(tasks) if v != view] == []:
            raise ValueError("keep at least one view in the list")
        self.hidden_views.add(view)

    def show_view(self, view):
        self.hidden_views.discard(view)

    def move_view(self, view, before=None):
        """Put a view just before another one (`before`), or last when it is None. Hidden views keep their place."""
        if view not in self.sequence or before == view or (before is not None and before not in self.sequence):
            return
        self.sequence.remove(view)
        self.sequence.insert(self.sequence.index(before) if before else len(self.sequence), view)

    def neighbours(self, view, tasks=True):
        """(the visible view before it, the visible view after it), None at an end: what Move left / Move right need."""
        vis = self.visible_views(tasks)
        if view not in vis:
            return None, None
        i = vis.index(view)
        return (vis[i - 1] if i else None), (vis[i + 1] if i + 1 < len(vis) else None)

    # ---- widgets of the view being looked at
    @property
    def d(self):
        return self.places.setdefault(self.mode, {})

    def use_mode(self, view):
        self.mode = view if self.free(view) else "overview"

    def get(self, key):
        return dict(self.d.get(key, {}))

    def hidden(self, key):
        return bool(self.d.get(key, {}).get("hidden"))

    def added(self, key):
        return bool(self.d.get(key, {}).get("added"))

    def position(self, key, default):
        """(x, y) where the widget goes: yours, or `default`."""
        e = self.d.get(key, {})
        return (e["x"], e["y"]) if "x" in e and "y" in e else tuple(default)

    def size(self, key, default):
        e = self.d.get(key, {})
        return (e["w"], e["h"]) if "w" in e and "h" in e else tuple(default)

    def _entry(self, key):
        return self.d.setdefault(key, {})

    def move(self, key, x, y):
        e = self._entry(key)
        e["x"], e["y"] = int(x), int(y)

    def resize(self, key, w, h):
        e = self._entry(key)
        e["w"], e["h"] = max(MIN_W, int(w)), max(MIN_H, int(h))

    def hide(self, key):
        e = self._entry(key)
        e["hidden"] = True
        e.pop("added", None)

    def show(self, key, default_hidden=False, pos=None):
        """Put a widget (back) on this view. `default_hidden` is true for the widgets that are not there by default:
        they are marked `added` so they stay after a restart."""
        e = self._entry(key)
        e.pop("hidden", None)
        if default_hidden:
            e["added"] = True
        if pos:
            e["x"], e["y"] = int(pos[0]), int(pos[1])
        if not e:
            self.d.pop(key, None)

    def forget(self, key):
        """Drop a widget from every view (it was deleted)."""
        for entries in self.places.values():
            entries.pop(key, None)

    def reset(self):
        """Forget the changes of this view."""
        self.places[self.mode] = {}

    def visible(self, key, default_visible):
        """Is the widget on the view? Default widgets unless hidden; the others only when added."""
        return not self.hidden(key) if default_visible else self.added(key)

    @staticmethod
    def clamp(x, y, w, h, screen, keep=48):
        """(x, y) moved so that at least `keep` pixels of the widget stay on the screen (it can never be lost off-screen)."""
        sw, sh = screen
        return (max(keep - w, min(int(x), sw - keep)), max(0, min(int(y), sh - keep)))

    def snapshot(self):
        """Everything that can change while you arrange (to go back to it: Cancel)."""
        import copy
        return copy.deepcopy((self.places, self.custom, self.hidden_views, self.sequence))

    def restore(self, snap):
        import copy
        self.places, self.custom, self.hidden_views, self.sequence = copy.deepcopy(snap)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 3, "custom": self.custom, "hidden": sorted(self.hidden_views), "order": self.sequence,
                       "places": {v: self.places.get(v, {}) for v in list(FREE_BUILTIN) + list(self.custom)}}, f, indent=1)
        os.replace(tmp, self.path)


def main(argv):
    lay = Layout()
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "list":
        print("\n".join(lay.title(v) for v in lay.visible_views()))
    elif cmd == "hidden":
        print("\n".join(lay.title(v) for v in lay.hidden_list()))
    else:
        print("usage: layout.py [list | hidden]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
