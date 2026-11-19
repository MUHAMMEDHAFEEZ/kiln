#!/usr/bin/python3
"""The widget gallery: where you add, remove and manage the widgets of a view.

    Create new      Note, Command output, Scripts, Shortcuts, Web page, Picture
    Kiln widgets    the cards that come with Kiln (Prayer, Today, GitHub, the terminal, Git, System...)
    My widgets      the ones you built: edit, duplicate, remove from the view, delete

Every tile is one click: it adds the widget to the view you are arranging (or takes it off), and the desktop changes at once while
the gallery stays open. The gallery knows nothing about windows: it asks a controller (`ctl`, see Gallery.__init__) for what to show
and tells it what you chose. It draws with the helpers of app.py, like reader.py (`bind(app)` gives it the module)."""
import math

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

K = None                       # the app module (set by bind)


def bind(app_module):
    global K
    K = app_module


TILE_W, TILE_H = 232, 92


class Tile(Gtk.DrawingArea):
    """One widget (or one kind of widget you can create), drawn like the cards of the desktop."""

    def __init__(self, entry, on_click, on_menu=None):
        super().__init__()
        self.entry, self.on_click, self.on_menu = entry, on_click, on_menu
        self.hover = None                                   # "tile", "menu" or None
        self.set_size_request(TILE_W, TILE_H)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_press)
        self.connect("motion-notify-event", lambda _w, ev: self._set(self._zone(ev.x, ev.y)))
        self.connect("leave-notify-event", lambda *_: self._set(None))
        if entry.get("reason"):
            self.set_tooltip_text(entry["reason"])

    def _zone(self, x, y):
        if self.on_menu and x > TILE_W - 40 and y > TILE_H - 36:
            return "menu"
        return "tile"

    def _set(self, z):
        if z != self.hover:
            self.hover = z
            self.queue_draw()
            win = self.get_window()
            if win:
                ok = self.entry.get("ok", True)
                win.set_cursor(Gdk.Cursor.new_from_name(Gdk.Display.get_default(), "pointer") if z and ok else None)

    def on_press(self, _w, ev):
        z = self._zone(ev.x, ev.y)
        if ev.button == 3 and self.on_menu:
            self.on_menu(self.entry, ev)
        elif ev.button == 1 and z == "menu":
            self.on_menu(self.entry, ev)
        elif ev.button == 1 and self.entry.get("ok", True):
            self.on_click(self.entry)
        return True

    def matches(self, q):
        e = self.entry
        return not q or q in e["title"].lower() or q in e.get("desc", "").lower()

    def on_draw(self, _w, cr):
        C, e = K.C, self.entry
        on, ok, create = e.get("on"), e.get("ok", True), e.get("group") == "create"
        hot = self.hover is not None and ok
        K.rrect(cr, 0.5, 0.5, TILE_W - 1, TILE_H - 1, 16)
        K.set_rgba(cr, C["ink"], 0.10 if hot else 0.05)
        cr.fill_preserve()
        K.set_rgba(cr, C["acc"] if on else C["border"], 0.9 if on else 0.55)
        cr.set_line_width(1.6 if on else 1)
        cr.stroke()
        alpha = 1.0 if ok else 0.45
        K.glyph(cr, e.get("icon", "folder"), 16, 16, 22, C["acc"] if on or create else C["acc2"], alpha)
        K.text(cr, e["title"], 50, 32, 14, C["text"], bold=True, maxw=TILE_W - 120, alpha=alpha)
        K.text_wrap(cr, e.get("desc", ""), 16, 58, TILE_W - 32 - (28 if self.on_menu else 0), 11.5, C["dim"], lines=2, alpha=alpha)
        if create:
            K.text(cr, "+", TILE_W - 18, 31, 20, C["acc"] if hot else C["dim"], align="r")
        elif not ok:
            K.pill(cr, TILE_W - 12, 31, "n/a", 10.5, C["dim"], bg_alpha=0.12, align="r")
        elif on:
            K.pill(cr, TILE_W - 12, 31, "✓ On", 11, C["acc"], bg_alpha=0.22, align="r", bold=True)
        else:
            K.pill(cr, TILE_W - 12, 31, "Add", 11, C["text"] if hot else C["dim"], bg_alpha=0.10, align="r")
        if self.on_menu:
            mh = self.hover == "menu"
            K.text(cr, "⋯", TILE_W - 20, TILE_H - 12, 18, C["acc"] if mh else C["dim"], align="r")
        return False


class Gallery(Gtk.Window):
    """The window. `ctl` provides:
        title()                     the name of the view being arranged
        entries()                   [{key, title, desc, icon, group: 'native'|'mine', on, ok, reason}]
        kinds()                     [{key: type, title, desc, icon}] of what you can create
        toggle(key), create(kind), edit(key), duplicate(key), delete(key), closed()"""

    def __init__(self, ctl):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.ctl = ctl
        self.set_title("Kiln Widgets")
        self.get_style_context().add_class("kiln")
        self.set_app_paintable(True)
        vis = self.get_screen().get_rgba_visual()
        if vis:
            self.set_visual(vis)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.W, self.H = 790, 640
        self.set_default_size(self.W, self.H)
        self.connect("draw", lambda _w, cr: (cr.set_operator(K.cairo.OPERATOR_CLEAR), cr.paint(),
                                              cr.set_operator(K.cairo.OPERATOR_OVER),
                                              K.card_bg(cr, 0, 0, self.W, self.H, 24, alpha=0.985), False)[-1])
        self.connect("key-press-event", lambda _w, ev: (self.close_gallery(), True)[-1] if ev.keyval == Gdk.KEY_Escape else False)
        self.connect("button-press-event", self._drag)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.q = ""
        self.sections = []                                     # (header label, flowbox)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for side in ("start", "end"):
            getattr(outer, f"set_margin_{side}")(26)
        outer.set_margin_top(22)
        outer.set_margin_bottom(22)
        self.add(outer)
        head = Gtk.Box(spacing=10)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.title_label = Gtk.Label(label="Widgets", xalign=0)
        self.title_label.get_style_context().add_class("kiln-gtitle")
        self.sub_label = Gtk.Label(label="", xalign=0)
        self.sub_label.get_style_context().add_class("kiln-gsub")
        titles.pack_start(self.title_label, False, False, 0)
        titles.pack_start(self.sub_label, False, False, 0)
        done = Gtk.Button(label="Done")
        done.get_style_context().add_class("kiln-primary")
        done.connect("clicked", lambda _b: self.close_gallery())
        head.pack_start(titles, True, True, 0)
        head.pack_end(done, False, False, 0)
        outer.pack_start(head, False, False, 0)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search widgets")
        self.search.get_style_context().add_class("kiln-search")
        self.search.set_margin_top(14)
        self.search.set_margin_bottom(10)
        self.search.connect("search-changed", self._searched)
        outer.pack_start(self.search, False, False, 0)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.get_style_context().add_class("kiln-notes")
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.scroll.add(self.body)
        outer.pack_start(self.scroll, True, True, 0)
        self.refresh()

    def _drag(self, _w, ev):
        if ev.button == 1 and ev.y < 60:
            self.begin_move_drag(1, int(ev.x_root), int(ev.y_root), ev.time)
        return False

    # ---- content
    def refresh(self):
        for child in self.body.get_children():
            child.destroy()
        self.sections = []
        self.title_label.set_text("Widgets")
        self.sub_label.set_text(f"Add them to “{self.ctl.title()}”, or take them off. The desktop changes as you click.")
        entries = self.ctl.entries()
        self._section("CREATE NEW", [Tile(dict(k, group="create", ok=True), lambda e: self.ctl.create(e["key"]) or self.refresh())
                                     for k in self.ctl.kinds()])
        self._section("KILN WIDGETS", [Tile(e, self._toggle) for e in entries if e["group"] == "native"])
        mine = [Tile(e, self._toggle, self._menu) for e in entries if e["group"] == "mine"]
        self._section("MY WIDGETS", mine, empty="Nothing here yet: create a note, a script, a web page... above, and it stays "
                                                "in this list so you can put it on any view.")
        self.body.show_all()
        self._filter()

    def _section(self, name, tiles, empty=""):
        label = Gtk.Label(label=name, xalign=0)
        label.get_style_context().add_class("kiln-section")
        label.set_margin_top(12)
        label.set_margin_bottom(6)
        self.body.pack_start(label, False, False, 0)
        fb = Gtk.FlowBox()
        fb.set_selection_mode(Gtk.SelectionMode.NONE)
        fb.set_min_children_per_line(3)
        fb.set_max_children_per_line(3)
        fb.set_row_spacing(10)
        fb.set_column_spacing(10)
        fb.set_homogeneous(True)
        fb.set_halign(Gtk.Align.START)
        for t in tiles:
            fb.add(t)
        for ch in fb.get_children():
            ch.set_can_focus(False)
        self.body.pack_start(fb, False, False, 0)
        note = None
        if not tiles and empty:
            note = Gtk.Label(label=empty, xalign=0)
            note.set_line_wrap(True)
            note.set_max_width_chars(70)
            note.get_style_context().add_class("kiln-gsub")
            self.body.pack_start(note, False, False, 0)
        self.sections.append((label, fb, note))

    def _toggle(self, entry):
        self.ctl.toggle(entry["key"])
        self.refresh()

    def _menu(self, entry, ev):
        key = entry["key"]
        items = [("Edit…", lambda: (self.ctl.edit(key), self.refresh())),
                 ("Duplicate", lambda: (self.ctl.duplicate(key), self.refresh())),
                 ("Take off this view" if entry["on"] else "Add to this view", lambda: self._toggle(entry)),
                 (None, None), ("Delete…", lambda: (self.ctl.delete(key), self.refresh()))]
        K.popup_menu(ev, items)

    def _searched(self, entry):
        self.q = entry.get_text().strip().lower()
        self._filter()

    def _filter(self):
        for label, fb, note in self.sections:
            shown = 0
            for child in fb.get_children():
                tile = child.get_child()
                ok = tile.matches(self.q)
                child.set_visible(ok)
                shown += 1 if ok else 0
            label.set_visible(shown > 0 or (not fb.get_children() and not self.q))
            fb.set_visible(shown > 0)
            if note is not None:
                note.set_visible(not self.q)

    # ---- showing it
    def popup(self, x, y):
        self.refresh()
        self.scroll.get_vadjustment().set_value(0)
        self.move(int(x), int(y))
        self.show_all()
        self.present()
        self.search.grab_focus()

    def close_gallery(self):
        self.hide()
        self.ctl.closed()
