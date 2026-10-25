#!/usr/bin/python3
"""Kiln Settings: a graphical setup program for the Kiln desktop.

    settings.py             open the settings
    settings.py --welcome   open it on the first page with a short introduction
    settings.py accounts    open it on one page (themes, general, projects, accounts, integrations, lock, health)

Every change is written to ~/.config/kiln/config.json right away. "Restart Kiln" makes the
desktop widgets pick it up (the top bar re-reads it at the next login, the lock screen the
next time it locks).
"""
import json
import os
import shutil
import subprocess
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

HOME = os.path.expanduser("~")
SHARE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SHARE)
import theme as T  # noqa: E402  (the theme engine: themes, wallpapers, applying)
import accounts as AC  # noqa: E402  (task and calendar services)
import prayer as PR  # noqa: E402  (calculation methods)

CONF = T.APP
CONFIG = f"{CONF}/config.json"

DEFAULTS = {
    "desktop_name": "{Host}'s Desktop", "clock": "12h", "desktop_dir": "",
    "project_dirs": ["~/Desktop"], "project_exclude": [],
    "web_search": {"name": "DuckDuckGo", "url": "https://duckduckgo.com/?q={q}"},
    "location": {"city": "", "latitude": None, "longitude": None},
    "prayer": {"enabled": False, "method": "mwl", "asr": "standard", "language": "en"},
    "now_playing": {"youtube_music_only": False},
    "lock_screen": {"widgets": True, "brightness": 0.72, "blur": 0},
    "terminal": {"project_commands": True},
    "modes": {"focus_mutes_notifications": True},
    "icons": {"recent_dot": True, "recent_hours": 48},
    "dock": {"cards": ["git", "system", "day", "notes", "tests"], "position": "left"},
    "week_start": "sun",
    "performance": "auto",
    "updates": {"mode": "notify"},
    "integrations": {"github": True, "lark": False, "claude_code": True},
    "theme_apply": {"gtk": True, "dock": True, "gnome_terminal": True, "wallpaper": True},
}
SEARCH = [("DuckDuckGo", "https://duckduckgo.com/?q={q}"), ("Google", "https://www.google.com/search?q={q}"),
          ("Brave Search", "https://search.brave.com/search?q={q}"), ("Bing", "https://www.bing.com/search?q={q}"),
          ("Startpage", "https://www.startpage.com/do/search?q={q}")]
CITIES = [("Cairo", 30.0444, 31.2357), ("Alexandria", 31.2001, 29.9187), ("Riyadh", 24.7136, 46.6753),
          ("Makkah", 21.3891, 39.8579), ("Madinah", 24.5247, 39.5692), ("Jeddah", 21.4858, 39.1925),
          ("Dubai", 25.2048, 55.2708), ("Abu Dhabi", 24.4539, 54.3773), ("Doha", 25.2854, 51.5310),
          ("Kuwait City", 29.3759, 47.9774), ("Amman", 31.9454, 35.9284), ("Baghdad", 33.3152, 44.3661),
          ("Casablanca", 33.5731, -7.5898), ("Tunis", 36.8065, 10.1815), ("Istanbul", 41.0082, 28.9784),
          ("London", 51.5072, -0.1276), ("Paris", 48.8566, 2.3522), ("Berlin", 52.5200, 13.4050),
          ("New York", 40.7128, -74.0060), ("Toronto", 43.6532, -79.3832), ("Karachi", 24.8607, 67.0011),
          ("Kuala Lumpur", 3.1390, 101.6869), ("Jakarta", -6.2088, 106.8456), ("Sydney", -33.8688, 151.2093)]
METHODS = list(PR.METHOD_NAMES)

CSS = b"""
.kiln-title { font-size: 20px; font-weight: 700; }
.kiln-sub { opacity: 0.65; }
.kiln-section { font-weight: 700; margin-top: 14px; }
.kiln-card { border-radius: 14px; padding: 8px; border: 2px solid transparent; }
.kiln-card:hover { background: alpha(@theme_fg_color, 0.06); }
flowboxchild:selected .kiln-card { border-color: @theme_selected_bg_color; background: alpha(@theme_selected_bg_color, 0.14); }
flowboxchild, flowboxchild:selected { background: transparent; outline: none; }
.kiln-pill { border-radius: 99px; padding: 2px 10px; background: alpha(@theme_selected_bg_color, 0.2); }
.kiln-mono { font-family: monospace; font-size: 12px; }
.kiln-ok { color: #3f9d5b; }
.kiln-bad { color: #d4503a; }
.kiln-svc { border-radius: 14px; padding: 12px 14px; background: alpha(@theme_fg_color, 0.05); }
.kiln-dot { border-radius: 99px; min-width: 12px; min-height: 12px; }
"""


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_file():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_file(cfg):
    os.makedirs(CONF, exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, CONFIG)


def run_quiet(*cmd, timeout=8):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def label(text, css=None, wrap=False, xalign=0.0):
    lb = Gtk.Label(label=text, xalign=xalign)
    if css:
        lb.get_style_context().add_class(css)
    if wrap:
        lb.set_line_wrap(True)
        lb.set_max_width_chars(70)
    return lb


class Settings(Gtk.Window):
    def __init__(self, welcome=False):
        super().__init__(title="Kiln Settings")
        self.set_default_size(980, 700)
        self.set_icon_name("kiln")
        self.cfg = deep_merge(DEFAULTS, load_file())
        self.raw = load_file()           # unknown keys the user added by hand survive a save
        self.dirty = False
        self.themes = T.load_themes()
        self.current = (open(T.CURRENT).read().strip() if os.path.exists(T.CURRENT) else "kiln-dark")
        self.welcome = welcome

        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.accent_prov = Gtk.CssProvider()               # this window wears the current theme's accent
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self.accent_prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.refresh_accent()

        hb = Gtk.HeaderBar(show_close_button=True, title="Kiln Settings")
        self.set_titlebar(hb)
        self.restart_btn = Gtk.Button(label="Restart Kiln")
        self.restart_btn.set_tooltip_text("Reload the desktop widgets with the saved settings")
        self.restart_btn.connect("clicked", lambda *_: self.restart())
        hb.pack_end(self.restart_btn)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)
        self.bar = Gtk.InfoBar(message_type=Gtk.MessageType.INFO, revealed=False)
        self.bar_label = Gtk.Label(label="", xalign=0)
        self.bar.get_content_area().add(self.bar_label)
        self.bar.add_button("Restart Kiln", 1)
        self.bar.connect("response", lambda *_: self.restart())
        self.bar.set_show_close_button(True)
        self.bar.connect("close", lambda *_: self.bar.set_revealed(False))
        outer.pack_start(self.bar, False, False, 0)

        body = Gtk.Box()
        outer.pack_start(body, True, True, 0)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        side = Gtk.StackSidebar(stack=self.stack)
        side.set_size_request(190, -1)
        body.pack_start(side, False, False, 0)
        body.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        body.pack_start(self.stack, True, True, 0)

        self.page("themes", "Appearance", self.build_themes())
        self.page("general", "General", self.build_general())
        self.page("projects", "Projects & files", self.build_projects())
        self.page("accounts", "Accounts", self.build_accounts())
        self.page("integrations", "Integrations", self.build_integrations())
        self.page("lock", "Lock screen & prayer", self.build_lock())
        self.page("health", "Health check", self.build_health())
        self.show_all()

    def refresh_accent(self):
        c = (self.themes.get("themes", {}).get(self.current) or {}).get("colors")
        if not c:
            return
        sel, on, acc = "#" + c["sel"], "#" + c["on_sel"], "#" + c["acc"]
        css = f"""
        stacksidebar row:selected, stacksidebar row:selected:hover {{ background-color: {sel}; color: {on}; }}
        switch:checked {{ background-image: none; background-color: {sel}; border-color: {sel}; }}
        button.suggested-action {{ background-image: none; background-color: {sel}; color: {on}; border-color: {sel}; }}
        button.suggested-action:hover {{ background-image: none; background-color: alpha({sel}, 0.85); }}
        scale highlight, scale fill {{ background-image: none; background-color: {sel}; border-color: {sel}; }}
        scale slider:hover {{ border-color: {sel}; }}
        progressbar progress {{ background-image: none; background-color: {sel}; }}
        link, .kiln-pill {{ color: {acc}; }}
        """
        try:
            self.accent_prov.load_from_data(css.encode())
        except GLib.Error:
            pass

    # ------------------------------------------------------------ plumbing
    def page(self, name, title, widget):
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for m in ("start", "end"):
            getattr(box, f"set_margin_{m}")(28)
        box.set_margin_top(22)
        box.set_margin_bottom(22)
        box.pack_start(label(title, "kiln-title"), False, False, 0)
        box.pack_start(widget, True, True, 0)
        sw.add(box)
        self.stack.add_titled(sw, name, title)

    def set(self, path, value):
        """cfg['a']['b'] = value, saved at once."""
        d = self.cfg
        keys = path.split(".")
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value
        self.raw = deep_merge(self.raw, {})
        r = self.raw
        for k in keys[:-1]:
            r = r.setdefault(k, {})
        r[keys[-1]] = value
        save_file(self.raw if self.raw else self.cfg)
        self.changed("Saved. Restart Kiln to apply it to the widgets.")

    def changed(self, msg):
        self.bar_label.set_text(msg)
        self.bar.set_revealed(True)
        self.restart_btn.get_style_context().add_class("suggested-action")

    def row(self, title, widget, hint=None):
        box = Gtk.Box(spacing=16)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left.pack_start(label(title), False, False, 0)
        if hint:
            left.pack_start(label(hint, "kiln-sub", wrap=True), False, False, 0)
        box.pack_start(left, True, True, 0)
        widget.set_valign(Gtk.Align.CENTER)
        box.pack_end(widget, False, False, 0)
        box.set_margin_top(6)
        return box

    def switch(self, path, title, hint=None, after=None):
        d = self.cfg
        for k in path.split("."):
            d = d.get(k, False) if isinstance(d, dict) else False
        sw = Gtk.Switch(active=bool(d))
        sw.connect("notify::active", lambda s, _p: (self.set(path, s.get_active()), after and after()))
        return self.row(title, sw, hint)

    def restart(self):
        self.restart_btn.get_style_context().remove_class("suggested-action")
        self.bar.set_revealed(False)
        threading.Thread(target=lambda: run_quiet(f"{SHARE}/start.sh", timeout=30), daemon=True).start()

    # ------------------------------------------------------------ appearance
    def build_themes(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        if self.welcome:
            box.pack_start(label("Welcome to Kiln. Pick a look, then look through the other pages to set your "
                                 "name, projects and city. Everything can be changed later from here.",
                                 wrap=True), False, False, 0)
        box.pack_start(label("Choose a theme. It restyles Kiln's widgets, the top bar and the lock screen, and "
                             "(optionally) the rest of the system.", "kiln-sub", wrap=True), False, False, 0)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, max_children_per_line=4,
                                min_children_per_line=2, homogeneous=True, column_spacing=10, row_spacing=10)
        self.flow.set_margin_top(10)
        self.cards = {}
        for name in self.themes["order"]:
            t = self.themes["themes"][name]
            card = self.theme_card(name, t)
            self.flow.add(card)
            self.cards[name] = card
        self.flow.connect("child-activated", lambda _f, c: self.apply_theme(c.kiln_name))
        self.flow.connect("selected-children-changed", self.on_select)
        box.pack_start(self.flow, False, False, 0)
        self.sel = self.current

        bar = Gtk.Box(spacing=8)
        self.apply_btn = Gtk.Button(label="Apply theme")
        self.apply_btn.get_style_context().add_class("suggested-action")
        self.apply_btn.connect("clicked", lambda *_: self.apply_theme(self.sel))
        self.pick_btn = Gtk.Button(label="Use my own wallpaper…")
        self.pick_btn.connect("clicked", lambda *_: self.pick_wallpaper())
        self.reset_btn = Gtk.Button(label="Back to the built-in wallpaper")
        self.reset_btn.connect("clicked", lambda *_: self.reset_wallpaper())
        for b in (self.apply_btn, self.pick_btn, self.reset_btn):
            bar.pack_start(b, False, False, 0)
        bar.set_margin_top(12)
        box.pack_start(bar, False, False, 0)
        self.status = label("", "kiln-sub")
        box.pack_start(self.status, False, False, 0)

        box.pack_start(label("What a theme may change outside Kiln", "kiln-section"), False, False, 0)
        for key, title, hint in (
                ("gtk", "GTK and icon theme, light/dark preference", None),
                ("dock", "Ubuntu Dock colours", None),
                ("gnome_terminal", "Colours of the default GNOME Terminal profile", None),
                ("wallpaper", "Desktop wallpaper", "Turn this off to keep your own picture.")):
            box.pack_start(self.switch(f"theme_apply.{key}", title, hint), False, False, 0)
        GLib.idle_add(self.select_current)
        return box

    def theme_card(self, name, t):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        v.get_style_context().add_class("kiln-card")
        img = Gtk.Image()
        self.fill_thumb(img, name, t)
        v.pack_start(img, False, False, 0)
        row = Gtk.Box(spacing=6)
        title = label(t.get("label", name), xalign=0.0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        row.pack_start(title, True, True, 0)
        for col in (t["colors"]["acc"], t["colors"]["card"], t["colors"]["text"]):
            sw = Gtk.DrawingArea()
            sw.set_size_request(14, 14)
            rgba = Gdk.RGBA()
            rgba.parse("#" + col)
            sw.connect("draw", lambda w, cr, rgba=rgba: (cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 1),
                                                          cr.arc(7, 7, 6.5, 0, 6.2832), cr.fill_preserve(),
                                                          cr.set_source_rgba(0.5, 0.5, 0.5, 0.5), cr.set_line_width(1),
                                                          cr.stroke(), False)[-1])
            row.pack_start(sw, False, False, 0)
        v.pack_start(row, False, False, 0)
        child = Gtk.FlowBoxChild()
        child.add(v)
        child.kiln_name = name
        child.kiln_img = img
        child.kiln_title = title
        return child

    def fill_thumb(self, img, name, t):
        path = None
        try:
            path = T.wallpaper_for(name, t)
        except Exception:
            pass
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 196, 110, False) if path else None
        except GLib.Error:
            pb = None
        if pb:
            img.set_from_pixbuf(pb)
        else:
            img.set_size_request(196, 110)

    def select_current(self):
        c = self.cards.get(self.current)
        if c:
            self.flow.select_child(c)
        self.refresh_cards()
        return False

    def refresh_cards(self):
        for n, c in self.cards.items():
            t = self.themes["themes"][n]
            c.kiln_title.set_text(t.get("label", n) + ("   ● current" if n == self.current else ""))
        own = self.own_wallpaper(self.sel)
        self.reset_btn.set_sensitive(bool(own))

    def on_select(self, *_):
        ch = self.flow.get_selected_children()
        if ch:
            self.sel = ch[0].kiln_name
            self.refresh_cards()

    def own_wallpaper(self, name):
        for ext in ("png", "jpg", "jpeg", "webp"):
            p = f"{T.WALL_DIR}/{name}.{ext}"
            if os.path.exists(p):
                return p
        return None

    def apply_theme(self, name):
        self.apply_btn.set_sensitive(False)
        self.status.set_text(f"Applying {self.themes['themes'][name].get('label', name)}…")

        def work():
            r = run_quiet("/usr/bin/python3", f"{SHARE}/theme.py", name, timeout=60)
            ok = r is not None and r.returncode == 0
            GLib.idle_add(self.theme_done, name, ok, (r.stderr if r else "") or "")
        threading.Thread(target=work, daemon=True).start()

    def theme_done(self, name, ok, err):
        self.apply_btn.set_sensitive(True)
        if ok:
            self.current = name
            self.refresh_accent()
            self.status.set_text("Applied. The top bar and lock screen update at the next login.")
        else:
            self.status.set_text("Could not apply the theme: " + err.strip()[-200:])
        self.refresh_cards()
        return False

    def pick_wallpaper(self):
        dlg = Gtk.FileChooserNative(title="Choose a wallpaper", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        flt = Gtk.FileFilter()
        flt.set_name("Pictures")
        for pat in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            flt.add_pattern(pat)
        dlg.add_filter(flt)
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            src = dlg.get_filename()
            ext = os.path.splitext(src)[1].lower().lstrip(".") or "png"
            os.makedirs(T.WALL_DIR, exist_ok=True)
            self.reset_wallpaper(apply=False)
            shutil.copy(src, f"{T.WALL_DIR}/{self.sel}.{ext}")
            self.fill_thumb(self.cards[self.sel].kiln_img, self.sel, self.themes["themes"][self.sel])
            if self.sel == self.current:
                self.apply_theme(self.sel)
            self.refresh_cards()

    def reset_wallpaper(self, apply=True):
        p = self.own_wallpaper(self.sel)
        while p:
            os.remove(p)
            p = self.own_wallpaper(self.sel)
        self.fill_thumb(self.cards[self.sel].kiln_img, self.sel, self.themes["themes"][self.sel])
        self.refresh_cards()
        if apply and self.sel == self.current:
            self.apply_theme(self.sel)

    # ------------------------------------------------------------ general
    def build_general(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        ent = Gtk.Entry(text=self.cfg["desktop_name"], width_chars=26)
        ent.connect("changed", lambda e: self.set("desktop_name", e.get_text()))
        box.pack_start(self.row("Desktop name", ent,
                                "Shown in the top bar. {Host} is this computer's name, {user} your login name."),
                       False, False, 0)
        clock = Gtk.ComboBoxText()
        clock.append("12h", "12-hour (8:30 PM)")
        clock.append("24h", "24-hour (20:30)")
        clock.set_active_id("24h" if str(self.cfg["clock"]).startswith("24") else "12h")
        clock.connect("changed", lambda c: self.set("clock", c.get_active_id()))
        box.pack_start(self.row("Clock", clock), False, False, 0)

        ws = self.cfg["web_search"]
        combo = Gtk.ComboBoxText()
        for n, _u in SEARCH:
            combo.append(n, n)
        combo.append("custom", "Custom…")
        known = next((n for n, u in SEARCH if u == ws.get("url")), None)
        combo.set_active_id(known or "custom")
        box.pack_start(self.row("Web search in the launcher (Super+K)", combo), False, False, 0)
        self.search_url = Gtk.Entry(text=ws.get("url", ""), width_chars=44)
        self.search_url.set_placeholder_text("https://example.com/search?q={q}")
        self.search_row = self.row("Custom search address", self.search_url, "Use {q} where the search words go.")
        box.pack_start(self.search_row, False, False, 0)

        def on_combo(c):
            cid = c.get_active_id()
            if cid == "custom":
                self.search_row.show()
                return
            url = dict(SEARCH)[cid]
            self.search_url.set_text(url)
            self.set("web_search", {"name": cid, "url": url})
            self.search_row.hide()
        combo.connect("changed", on_combo)
        self.search_url.connect("changed", lambda e: combo.get_active_id() == "custom" and
                                self.set("web_search", {"name": "Web search", "url": e.get_text()}))
        self.search_row.show_all()
        if known:
            self.search_row.set_no_show_all(True)
            self.search_row.hide()
        wk = Gtk.ComboBoxText()
        for k, n in (("sun", "Sunday"), ("sat", "Saturday"), ("mon", "Monday")):
            wk.append(k, n)
        wk.set_active_id(self.cfg.get("week_start", "sun"))
        wk.connect("changed", lambda c: self.set("week_start", c.get_active_id()))
        box.pack_start(self.row("Calendar week starts on", wk), False, False, 0)

        dk = Gtk.ComboBoxText()
        for k, n in (("left", "Left (what Kiln is designed for)"), ("bottom", "Bottom"), ("right", "Right"),
                     ("", "Leave it as it is")):
            dk.append(k or "none", n)
        dk.set_active_id(self.cfg["dock"].get("position") or "none")
        dk.connect("changed", lambda c: self.set_dock_position(c.get_active_id()))
        box.pack_start(self.row("Ubuntu Dock position", dk,
                                "Kiln keeps the dock there, even if something moves it."), False, False, 0)
        self.build_updates(box)
        pf = Gtk.ComboBoxText()
        for k, n in (("auto", "Automatic"), ("low", "Light (older or slower computers)"), ("normal", "Full")):
            pf.append(k, n)
        pf.set_active_id(self.cfg.get("performance", "auto"))
        pf.connect("changed", lambda c: self.set("performance", c.get_active_id()))
        box.pack_start(self.row("Performance", pf,
                                "Light mode refreshes less often and scans less. Automatic picks it on "
                                "computers with two cores or less than 4 GB of memory."), False, False, 0)
        box.pack_start(label("Cards on big screens", "kiln-section"), False, False, 0)
        box.pack_start(label("Kiln places the cards that fit in the free space next to the terminal. "
                             "A laptop screen has none.", "kiln-sub", wrap=True), False, False, 0)
        for key, title, hint in (("git", "Git", "Changed files and recent commits of the current project."),
                                 ("system", "System", "CPU, memory, GPU, network, disk and the busiest programs."),
                                 ("day", "Today", "Calendar, Hijri date and prayer times (set your city under Lock screen & prayer)."),
                                 ("notes", "Notes", "A scratchpad for each project, and a global one."),
                                 ("tests", "Tests", "Result and output of the last test run, with a run button."),
                                 ("services", "Running", "Dev servers and containers (the work bar already shows them).")):
            sw = Gtk.Switch(active=key in self.cfg["dock"]["cards"])
            sw.connect("notify::active", lambda s_, _p, k=key: self.toggle_dock(k, s_.get_active()))
            box.pack_start(self.row(title, sw, hint), False, False, 0)
        box.pack_start(label("Desktop behaviour", "kiln-section"), False, False, 0)
        box.pack_start(self.switch("terminal.project_commands", "Terminal buttons from the project",
                                   "The buttons under the terminal come from the project's own files "
                                   "(package.json scripts, Makefile, manage.py, Cargo, Go, Flutter...)."),
                       False, False, 0)
        box.pack_start(self.switch("modes.focus_mutes_notifications", "Focus mode silences notifications",
                                   "Switch modes from the work bar, or with Super+J. Banners come back when you leave Focus."),
                       False, False, 0)
        box.pack_start(self.switch("icons.recent_dot", "Dot on recently changed desktop icons",
                                   "A small dot marks folders and files changed in the last two days."),
                       False, False, 0)
        return box

    def build_updates(self, box):
        import update as UP
        mode = Gtk.ComboBoxText()
        for k, n in (("auto", "Install them for me"), ("notify", "Tell me, then I press Update"), ("off", "Never check")):
            mode.append(k, n)
        mode.set_active_id(self.cfg.get("updates", {}).get("mode", "notify"))
        mode.connect("changed", lambda c: self.set("updates.mode", c.get_active_id()))
        box.pack_start(self.row("Kiln updates", mode, "New releases come from GitHub. Your settings are kept, and "
                                "the previous version is kept in case the new one does not start."), False, False, 0)
        row = Gtk.Box(spacing=10)
        self.upd_msg = label("", "kiln-sub", wrap=True)
        self.upd_btn = Gtk.Button(label="Check now")
        self.upd_btn.connect("clicked", lambda *_: self.update_clicked(UP))
        row.pack_start(self.upd_btn, False, False, 0)
        row.pack_start(self.upd_msg, True, True, 0)
        box.pack_start(row, False, False, 4)
        self.update_label(UP)

    def update_label(self, UP):
        st = UP.load_state()
        if st.get("newer"):
            self.upd_msg.set_text(f"Kiln {st['latest']} is available (you have {UP.installed()}).")
            self.upd_btn.set_label("Update now")
        else:
            self.upd_msg.set_text(f"You have Kiln {UP.installed()}." + (f" Last update: {st['last_result']}."
                                                                         if st.get("last_result") else ""))
            self.upd_btn.set_label("Check now")

    def update_clicked(self, UP):
        self.upd_btn.set_sensitive(False)
        self.upd_msg.set_text("Working…")
        install = UP.load_state().get("newer")

        def work():
            try:
                if install:
                    msg = UP.apply(UP.load_state()["latest"])
                else:
                    st = UP.check()
                    msg = st.get("error") or ("Kiln " + st["latest"] + " is available." if st.get("newer")
                                              else "Kiln is up to date.")
            except UP.UpdateError as e:
                msg = str(e)
            GLib.idle_add(lambda: (self.upd_btn.set_sensitive(True), self.update_label(UP), self.upd_msg.set_text(msg),
                                   False)[-1])
        threading.Thread(target=work, daemon=True).start()

    def set_dock_position(self, pos):
        pos = "" if pos == "none" else pos
        self.set("dock.position", pos)
        if pos:                                        # show the result at once
            run_quiet("gsettings", "set", "org.gnome.shell.extensions.dash-to-dock", "dock-position", pos.upper())

    def toggle_dock(self, key, on):
        order = ["git", "system", "day", "notes", "tests", "services"]
        cards = {c for c in self.cfg["dock"]["cards"] if c != key}
        if on:
            cards.add(key)
        self.set("dock.cards", [k for k in order if k in cards])

    # ------------------------------------------------------------ projects
    def build_projects(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.pack_start(label("Kiln looks for git projects in these folders (4 levels deep). They feed the "
                             "Projects card, the launcher and the work bar.", "kiln-sub", wrap=True), False, False, 0)
        self.dirs = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.dirs.get_style_context().add_class("frame")
        box.pack_start(self.dirs, False, False, 6)
        self.fill_dirs()
        add = Gtk.Button(label="Add a folder…")
        add.connect("clicked", self.add_dir)
        box.pack_start(add, False, False, 0)

        ex = Gtk.Entry(text=", ".join(self.cfg["project_exclude"]), width_chars=34)
        ex.set_placeholder_text("archive, OpenSource")
        ex.connect("changed", lambda e: self.set("project_exclude",
                                                 [x.strip() for x in e.get_text().split(",") if x.strip()]))
        box.pack_start(self.row("Skip folders named", ex, "Separated by commas. node_modules and vendor are always skipped."),
                       False, False, 0)

        box.pack_start(label("Desktop icons", "kiln-section"), False, False, 0)
        cur = self.cfg["desktop_dir"] or "(your Desktop folder)"
        self.desk_lbl = label(cur, "kiln-mono")
        b = Gtk.Box(spacing=8)
        pick = Gtk.Button(label="Choose…")
        pick.connect("clicked", self.pick_desktop)
        rst = Gtk.Button(label="Reset")
        rst.connect("clicked", lambda *_: (self.set("desktop_dir", ""), self.desk_lbl.set_text("(your Desktop folder)")))
        for w in (self.desk_lbl, pick, rst):
            b.pack_start(w, False, False, 0)
        box.pack_start(self.row("Folder shown as desktop icons", b), False, False, 0)
        return box

    def fill_dirs(self):
        for c in self.dirs.get_children():
            self.dirs.remove(c)
        for d in self.cfg["project_dirs"]:
            r = Gtk.ListBoxRow()
            h = Gtk.Box(spacing=8)
            h.set_margin_start(10)
            h.set_margin_end(6)
            h.set_margin_top(4)
            h.set_margin_bottom(4)
            h.pack_start(label(d, "kiln-mono"), True, True, 0)
            rm = Gtk.Button.new_from_icon_name("list-remove-symbolic", Gtk.IconSize.BUTTON)
            rm.set_relief(Gtk.ReliefStyle.NONE)
            rm.connect("clicked", lambda _b, d=d: self.remove_dir(d))
            h.pack_end(rm, False, False, 0)
            r.add(h)
            self.dirs.add(r)
        self.dirs.show_all()

    def remove_dir(self, d):
        self.cfg["project_dirs"] = [x for x in self.cfg["project_dirs"] if x != d]
        self.set("project_dirs", self.cfg["project_dirs"])
        self.fill_dirs()

    def add_dir(self, *_):
        dlg = Gtk.FileChooserNative(title="Add a projects folder", transient_for=self,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER)
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            p = dlg.get_filename()
            p = "~" + p[len(HOME):] if p.startswith(HOME) else p
            if p not in self.cfg["project_dirs"]:
                self.cfg["project_dirs"].append(p)
                self.set("project_dirs", self.cfg["project_dirs"])
                self.fill_dirs()

    def pick_desktop(self, *_):
        dlg = Gtk.FileChooserNative(title="Folder for the desktop icons", transient_for=self,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER)
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            p = dlg.get_filename()
            self.set("desktop_dir", p)
            self.desk_lbl.set_text(p)

    # ------------------------------------------------------------ accounts
    def build_accounts(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.pack_start(label("Connect the services you use. Their tasks appear in the Today card and their "
                             "meetings in Agenda. What you type here stays on this computer, in "
                             "~/.config/kiln/accounts.json, a file only you can read.", "kiln-sub", wrap=True),
                       False, False, 0)
        self.acct_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.pack_start(self.acct_box, False, False, 0)
        self.fill_accounts()
        return box

    def fill_accounts(self):
        for c in self.acct_box.get_children():
            self.acct_box.remove(c)
        on = AC.connected()
        cache = AC.read_cache().get("services", {})
        for sid, svc in AC.SERVICES.items():
            row = Gtk.Box(spacing=12)
            row.get_style_context().add_class("kiln-svc")
            dot = Gtk.DrawingArea()
            dot.set_size_request(12, 12)
            dot.set_valign(Gtk.Align.CENTER)
            col = Gdk.RGBA()
            col.parse("#" + svc.color)
            dot.connect("draw", lambda w, cr, c=col: (cr.set_source_rgba(c.red, c.green, c.blue, 1),
                                                      cr.arc(6, 6, 6, 0, 6.2832), cr.fill(), False)[-1])
            row.pack_start(dot, False, False, 0)
            mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            kinds = " and ".join({"tasks": "tasks", "events": "meetings"}[k] for k in svc.kinds)
            title = label(svc.name)
            title.set_markup(f"<b>{GLib.markup_escape_text(svc.name)}</b>  <small>{kinds}</small>")
            mid.pack_start(title, False, False, 0)
            c = cache.get(sid) or {}
            if sid in on:
                if c.get("error"):
                    status, css = c["error"], "kiln-bad"
                elif c:
                    bits = ([f"{len(c.get('tasks', []))} tasks"] if "tasks" in svc.kinds else []) + \
                           ([f"{len(c.get('events', []))} meetings"] if "events" in svc.kinds else [])
                    status, css = "Connected · " + " · ".join(bits), "kiln-ok"
                else:
                    status, css = "Connected · loading…", "kiln-ok"
            else:
                status, css = svc.tagline, "kiln-sub"
            mid.pack_start(label(status, css, wrap=True), False, False, 0)
            row.pack_start(mid, True, True, 0)
            if sid in on:
                off = Gtk.Button(label="Disconnect")
                off.connect("clicked", lambda _b, i=sid: self.disconnect_account(i))
                row.pack_end(off, False, False, 0)
            btn = Gtk.Button(label="Edit" if sid in on and not svc.cli else "Connect" if sid not in on else "Check")
            btn.connect("clicked", lambda _b, i=sid: self.connect_dialog(i))
            row.pack_end(btn, False, False, 0)
            self.acct_box.pack_start(row, False, False, 0)
        more = label("Missing a service? Each one is a small class in share/accounts.py (see docs/ACCOUNTS.md).",
                     "kiln-sub", wrap=True)
        self.acct_box.pack_start(more, False, False, 6)
        self.acct_box.show_all()

    def sync_accounts(self, then=None):
        def work():
            run_quiet("/usr/bin/python3", f"{SHARE}/accounts.py", "sync", timeout=60)
            GLib.idle_add(lambda: (self.fill_accounts(), then and then(), False)[-1])
        threading.Thread(target=work, daemon=True).start()

    def disconnect_account(self, sid):
        if sid == "lark":
            self.set("integrations.lark", False)
        data = AC.load_accounts()
        if sid in data:
            data.pop(sid)
            AC.save_accounts(data)
        self.sync_accounts()
        self.fill_accounts()

    def connect_dialog(self, sid):
        svc = AC.SERVICES[sid]
        dlg = Gtk.Dialog(title=f"Connect {svc.name}", transient_for=self, modal=True)
        dlg.set_default_size(520, -1)
        area = dlg.get_content_area()
        area.set_spacing(8)
        for m in ("start", "end", "top", "bottom"):
            getattr(area, f"set_margin_{m}")(18)
        head = label("", wrap=True)
        head.set_markup(f"<b>{GLib.markup_escape_text(svc.name)}</b>  {GLib.markup_escape_text(svc.tagline)}")
        area.pack_start(head, False, False, 0)
        steps = "\n".join(f"{i}.  {t}" for i, t in enumerate(svc.steps, 1))
        area.pack_start(label(steps, "kiln-mono" if svc.cli else "kiln-sub", wrap=True), False, False, 4)
        if svc.link:
            link = Gtk.Button(label="Open the page where you find it")
            link.set_halign(Gtk.Align.START)
            link.connect("clicked", lambda *_: run_quiet("xdg-open", svc.link))
            area.pack_start(link, False, False, 0)
        saved = AC.details(sid)
        widgets = {}
        for f in svc.fields:
            area.pack_start(label(f.label), False, False, 0)
            if f.multiline:
                tv = Gtk.TextView(wrap_mode=Gtk.WrapMode.CHAR, top_margin=6, bottom_margin=6, left_margin=6, right_margin=6)
                tv.get_style_context().add_class("kiln-mono")
                tv.get_buffer().set_text(saved.get(f.key, ""))
                frame = Gtk.ScrolledWindow(min_content_height=90, shadow_type=Gtk.ShadowType.IN)
                frame.add(tv)
                area.pack_start(frame, False, False, 0)
                widgets[f.key] = lambda t=tv: t.get_buffer().get_text(t.get_buffer().get_start_iter(),
                                                                       t.get_buffer().get_end_iter(), False)
            else:
                ent = Gtk.Entry(text=saved.get(f.key, ""), activates_default=True)
                ent.set_visibility(not f.secret)
                if f.hint:
                    ent.set_placeholder_text(f.hint)
                area.pack_start(ent, False, False, 0)
                widgets[f.key] = ent.get_text
            if f.hint and f.multiline:
                area.pack_start(label(f.hint, "kiln-sub", wrap=True), False, False, 0)
        msg = label("", wrap=True)
        area.pack_start(msg, False, False, 4)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok = dlg.add_button("Turn on" if svc.cli else "Test and save", Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)
        busy = {"on": False}

        def show(text, css):
            for k in ("kiln-ok", "kiln-bad", "kiln-sub"):
                msg.get_style_context().remove_class(k)
            msg.get_style_context().add_class(css)
            msg.set_text(text)

        def on_response(_d, resp):
            if resp != Gtk.ResponseType.OK:
                dlg.destroy()
                return
            if busy["on"]:
                return
            values = {k: g().strip() for k, g in widgets.items()}
            busy["on"] = True
            ok.set_sensitive(False)
            show("Checking…", "kiln-sub")

            def work():
                try:
                    AC.test(sid, values) if not svc.cli else svc.check({})
                    err = None
                except AC.ServiceError as e:
                    err = str(e)

                def done():
                    busy["on"] = False
                    ok.set_sensitive(True)
                    if err:
                        show(err, "kiln-bad")
                        return False
                    if svc.cli:
                        self.set("integrations.lark", True)
                    else:
                        data = AC.load_accounts()
                        data[sid] = values
                        AC.save_accounts(data)
                    dlg.destroy()
                    self.fill_accounts()
                    self.sync_accounts()
                    return False
                GLib.idle_add(done)
            threading.Thread(target=work, daemon=True).start()
        dlg.connect("response", on_response)
        dlg.show_all()

    # ------------------------------------------------------------ integrations
    def build_integrations(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(label("Switch off what you do not use: its card then explains how to turn it on.",
                             "kiln-sub", wrap=True), False, False, 0)
        gh = "installed" if shutil.which("gh") else "not installed (https://cli.github.com)"
        self.gh_status = label("", "kiln-sub")
        box.pack_start(self.switch("integrations.github", "GitHub",
                                   f"Notifications, pull requests and CI. The GitHub CLI is {gh}."), False, False, 0)
        row = Gtk.Box(spacing=8)
        self.gh_btn = Gtk.Button(label="Check GitHub login")
        self.gh_btn.connect("clicked", lambda *_: self.check_gh())
        row.pack_start(self.gh_btn, False, False, 0)
        row.pack_start(self.gh_status, False, False, 0)
        box.pack_start(row, False, False, 0)

        box.pack_start(self.switch("integrations.claude_code", "Claude Code tasks in the project tab",
                                   "Shows the tasks of your latest Claude Code session in each project."),
                       False, False, 0)
        self.hook_sw = Gtk.Switch(active=self.hook_installed())
        self.hook_sw.connect("notify::active", self.toggle_hook)
        box.pack_start(self.row("Let Claude Code read my project todos", self.hook_sw,
                                "Adds a SessionStart hook to ~/.claude/settings.json (it is backed up first)."),
                       False, False, 0)
        box.pack_start(label("Music", "kiln-section"), False, False, 0)
        box.pack_start(self.switch("now_playing.youtube_music_only", "Show YouTube Music only",
                                   "Ignore VLC, browser videos and other players in Now Playing."),
                       False, False, 0)
        return box

    def hook_installed(self):
        try:
            return "kiln/claude-hook.sh" in open(f"{HOME}/.claude/settings.json", encoding="utf-8").read()
        except OSError:
            return False

    def toggle_hook(self, sw, _p):
        tool = f"{SHARE}/claude-hook.py"
        if not os.path.exists(tool):
            return
        if sw.get_active() == self.hook_installed():
            return
        args = ["add", f"{SHARE}/claude-hook.sh"] if sw.get_active() else ["remove"]
        run_quiet("/usr/bin/python3", tool, *args)

    def check_gh(self):
        self.gh_status.set_text("checking…")

        def work():
            r = run_quiet("gh", "auth", "status", timeout=10)
            msg = "Logged in." if r and r.returncode == 0 else "Not logged in. Run: gh auth login"
            GLib.idle_add(self.gh_status.set_text, msg)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ lock screen and prayer
    def build_lock(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(self.switch("lock_screen.widgets", "Lock screen widgets",
                                   "Status chips, now playing, focus timer, prayer times. Off keeps GNOME's plain clock."),
                       False, False, 0)
        br = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.3, 1.0, 0.02)
        br.set_value(float(self.cfg["lock_screen"]["brightness"]))
        br.set_size_request(240, -1)
        br.connect("value-changed", lambda s: self.set("lock_screen.brightness", round(s.get_value(), 2)))
        box.pack_start(self.row("Wallpaper brightness behind the clock", br), False, False, 0)
        bl = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 40, 1)
        bl.set_value(float(self.cfg["lock_screen"]["blur"]))
        bl.set_size_request(240, -1)
        bl.connect("value-changed", lambda s: self.set("lock_screen.blur", int(s.get_value())))
        box.pack_start(self.row("Blur", bl, "0 keeps the wallpaper crisp."), False, False, 0)

        box.pack_start(label("Prayer times", "kiln-section"), False, False, 0)
        box.pack_start(label("Calculated on this computer from your location. No internet and no account.",
                             "kiln-sub"), False, False, 0)
        box.pack_start(self.switch("prayer.enabled", "Show the next prayer on the lock screen"), False, False, 0)

        city = Gtk.ComboBoxText.new_with_entry()
        for n, _a, _b in CITIES:
            city.append_text(n)
        city.get_child().set_text(self.cfg["location"].get("city", ""))
        box.pack_start(self.row("City", city, "Pick one, or type a name and fill in the numbers below."), False, False, 0)
        lat = Gtk.SpinButton.new_with_range(-90, 90, 0.0001)
        lat.set_digits(4)
        lat.set_value(self.cfg["location"].get("latitude") or 0)
        lon = Gtk.SpinButton.new_with_range(-180, 180, 0.0001)
        lon.set_digits(4)
        lon.set_value(self.cfg["location"].get("longitude") or 0)
        coords = Gtk.Box(spacing=8)
        coords.pack_start(label("lat"), False, False, 0)
        coords.pack_start(lat, False, False, 0)
        coords.pack_start(label("lon"), False, False, 0)
        coords.pack_start(lon, False, False, 0)
        box.pack_start(self.row("Latitude / longitude", coords), False, False, 0)

        def save_loc(*_):
            self.set("location", {"city": city.get_child().get_text(), "latitude": round(lat.get_value(), 4),
                                  "longitude": round(lon.get_value(), 4)})

        def on_city(c):
            name = c.get_child().get_text()
            hit = next((x for x in CITIES if x[0] == name), None)
            if hit:
                lat.set_value(hit[1])
                lon.set_value(hit[2])
            save_loc()
        city.connect("changed", on_city)
        lat.connect("value-changed", save_loc)
        lon.connect("value-changed", save_loc)

        meth = Gtk.ComboBoxText()
        for k, n in METHODS:
            meth.append(k, n)
        meth.set_active_id(self.cfg["prayer"].get("method", "mwl"))
        meth.connect("changed", lambda c: self.set("prayer.method", c.get_active_id()))
        box.pack_start(self.row("Calculation method", meth), False, False, 0)
        asr = Gtk.ComboBoxText()
        asr.append("standard", "Standard (Shafi'i, Maliki, Hanbali)")
        asr.append("hanafi", "Hanafi")
        asr.set_active_id(self.cfg["prayer"].get("asr", "standard"))
        asr.connect("changed", lambda c: self.set("prayer.asr", c.get_active_id()))
        box.pack_start(self.row("Asr", asr), False, False, 0)
        lang = Gtk.ComboBoxText()
        lang.append("ar", "العربية (الفجر، الظهر…)")
        lang.append("en", "English (Fajr, Dhuhr…)")
        lang.set_active_id(self.cfg["prayer"].get("language", "en"))
        lang.connect("changed", lambda c: self.set("prayer.language", c.get_active_id()))
        box.pack_start(self.row("Prayer names", lang), False, False, 0)
        return box

    # ------------------------------------------------------------ health
    def build_health(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.pack_start(label("Checks that Kiln's requirements and files are in place.", "kiln-sub"), False, False, 0)
        row = Gtk.Box(spacing=8)
        run = Gtk.Button(label="Run the check")
        run.connect("clicked", lambda *_: self.run_doctor())
        for text, cb in (("Open settings folder", lambda *_: run_quiet("xdg-open", CONF)),
                         ("Show log", lambda *_: self.show_log())):
            b = Gtk.Button(label=text)
            b.connect("clicked", cb)
            row.pack_start(b, False, False, 0)
        row.pack_start(run, False, False, 0)
        row.reorder_child(run, 0)
        box.pack_start(row, False, False, 0)
        self.doc = Gtk.TextView(editable=False, monospace=True, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD)
        self.doc.set_left_margin(10)
        self.doc.set_top_margin(8)
        sc = Gtk.ScrolledWindow(min_content_height=320, shadow_type=Gtk.ShadowType.IN)
        sc.add(self.doc)
        box.pack_start(sc, True, True, 0)
        GLib.idle_add(self.run_doctor)
        return box

    def kiln_cli(self):
        return shutil.which("kiln") or os.path.join(os.path.dirname(SHARE), "bin", "kiln")

    def run_doctor(self):
        self.doc.get_buffer().set_text("Checking…")

        def work():
            env = dict(os.environ, KILN_SHARE=SHARE)
            try:
                r = subprocess.run([self.kiln_cli(), "doctor"], capture_output=True, text=True, timeout=40, env=env)
                out = r.stdout + r.stderr
            except (OSError, subprocess.SubprocessError) as e:
                out = f"Could not run the check: {e}"
            GLib.idle_add(self.doc.get_buffer().set_text, out)
        threading.Thread(target=work, daemon=True).start()
        return False

    def show_log(self):
        def work():
            r = run_quiet("journalctl", "--user", "-u", "kiln", "--no-pager", "-n", "80", timeout=10)
            GLib.idle_add(self.doc.get_buffer().set_text, (r.stdout if r else "") or "(no log yet)")
        threading.Thread(target=work, daemon=True).start()


def main():
    win = Settings(welcome="--welcome" in sys.argv)
    pages = {"themes", "general", "projects", "accounts", "integrations", "lock", "health"}
    for a in sys.argv[1:]:
        if a in pages:
            win.stack.set_visible_child_name(a)
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    main()
