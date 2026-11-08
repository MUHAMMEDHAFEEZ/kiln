"""The Books mode: a library of your PDFs with covers and "continue reading", and a reader with bookmarks, highlights,
notes, search, a table of contents, night and sepia pages, and reading statistics.

Everything is drawn with the same helpers as the other Kiln cards (app.py gives them to `bind`). The PDF itself is only
touched by a worker thread (reader_core.py), so scrolling and typing never wait for a page.
"""
import bisect
import collections
import datetime
import math
import os
import threading
import time
import urllib.parse

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

import books as BK  # noqa: E402
import reader_core as RC  # noqa: E402

K = None                                    # the app module: colours (K.C) and drawing helpers; set by bind()


def bind(app_module):
    global K
    K = app_module


TOP, BOTTOM, PANEL_W, PAD = 56, 38, 340, 24
LIB_HEAD = 112                              # the part of the library that does not scroll: title, filters
HL_COLORS = {"yellow": "ffd54a", "green": "6ee7a0", "pink": "ff8fb8", "blue": "7cb6ff"}
THEME_LABEL = {"auto": "Auto", "light": "Light", "sepia": "Sepia", "night": "Night"}
STATUS_LABEL = (("all", "All"), ("reading", "Reading"), ("unread", "Unread"), ("finished", "Finished"))
SORT_LABEL = {"recent": "Recent", "title": "Title", "progress": "Progress"}
ZOOM_STEP, MIN_SCALE, MAX_SCALE = 1.18, 0.25, 6.0
FIT_MAX_W = 1000                            # "fit to width" never makes a page wider than this many pixels
PROBE = 24                                  # the place in the book is taken this far below the top of the view (never in a gap)
CARD_COVER_H = 196                          # a cover in the grid is 3:4


def hexmix(a, b, k):
    """Colour a mixed with colour b (k = share of b), as hex."""
    ra, rb = [int(a[i:i + 2], 16) for i in (0, 2, 4)], [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "%02x%02x%02x" % tuple(round(ra[i] * (1 - k) + rb[i] * k) for i in range(3))


class BooksView(Gtk.Overlay):
    """Library and reader in one window. `mode` is "library" or "reader"."""
    KEY = "booksview"

    def __init__(self, terminal=None):
        super().__init__()
        self.terminal = terminal
        self.W, self.H = 1500, 800
        self.cfg = BK.config()
        self.store, self.index = BK.Store(), BK.Index()
        self.tracker = BK.Tracker(self.store)
        self.store.prune()
        self.mode = "library"
        self.entries, self.shown = [], []
        self.query, self.status, self.sort = "", "all", "recent"
        self.lib_y = 0.0
        self.lib_h = 0.0
        self.covers, self.indexing, self.scanning = {}, False, False
        self.files, self.last_scan, self.loaded_now, self.rescan = [], 0.0, 0, False
        self.toast_text, self.toast_until = "", 0.0
        self.hits, self.hover = [], None
        self.theme_name = self.store.pref("theme", self.cfg["theme"])
        self.spread = self.store.pref("spread", self.cfg["spread"])      # two pages side by side
        self.reset_reader()

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(self.W, self.H)
        self.area.set_can_focus(True)
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                             Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON1_MOTION_MASK |
                             Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.SCROLL_MASK |
                             Gdk.EventMask.SMOOTH_SCROLL_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        self.area.connect("draw", self.on_draw)
        self.area.connect("button-press-event", self.on_press)
        self.area.connect("button-release-event", self.on_release)
        self.area.connect("motion-notify-event", self.on_motion)
        self.area.connect("leave-notify-event", lambda *_: self.set_hover(None))
        self.area.connect("scroll-event", self.on_scroll)
        self.area.connect("key-press-event", self.on_key)
        self.area.drag_dest_set(Gtk.DestDefaults.ALL, [Gtk.TargetEntry.new("text/uri-list", 0, 0)], Gdk.DragAction.COPY)
        self.area.connect("drag-data-received", self.on_drop)
        self.add(self.area)

        self.entry = Gtk.Entry()                       # filter (library), search and go-to-page (reader)
        self.entry.set_size_request(260, 34)
        self.entry.set_halign(Gtk.Align.START)
        self.entry.set_valign(Gtk.Align.START)
        self.entry.set_no_show_all(True)
        self.entry.get_style_context().add_class("kiln-entry")
        self.entry.connect("activate", self.on_entry_activate)
        self.entry.connect("changed", self.on_entry_changed)
        self.entry.connect("key-press-event", self.on_entry_key)
        self.entry_mode = None
        self.add_overlay(self.entry)
        self.connect("get-child-position", self.entry_position)

        GLib.timeout_add_seconds(K.every(2), self.tick)
        self.refresh()

    # ------------------------------------------------------------------ plumbing
    def set_width(self, w):
        self.set_size(int(w), self.H)

    def set_height(self, h):
        self.set_size(self.W, int(h))

    def set_size(self, w, h):
        changed = (w, h) != (self.W, self.H)
        self.W, self.H = w, h
        self.area.set_size_request(w, h)
        self.queue_resize()
        if changed and self.layout and self.fit:
            self.apply_zoom(None, keep=True)
        self.area.queue_draw()

    def region(self, key, x, y, w, h, hot=True):
        self.hits.append(((x, y, w, h), key))
        if hot and self.hover == key:
            K.rrect(self.cr, x, y, w, h, 8)
            K.set_rgba(self.cr, K.C["ink"], 0.08)
            self.cr.fill()

    def hit(self, px, py):
        for (x, y, w, h), key in reversed(self.hits):
            if x <= px < x + w and y <= py < y + h:
                return key
        return None

    def set_hover(self, key):
        if key != self.hover:
            self.hover = key
            self.area.queue_draw()

    def cursor(self, name):
        win = self.area.get_window()
        if win and getattr(self, "_cursor_name", None) != name:
            self._cursor_name = name
            win.set_cursor(Gdk.Cursor.new_from_name(Gdk.Display.get_default(), name) if name else None)

    def toast(self, text, secs=4.0):
        self.toast_text, self.toast_until = text, time.time() + secs
        self.area.queue_draw()
        GLib.timeout_add(int(secs * 1000) + 100, lambda: (self.area.queue_draw(), False)[1])

    def dpr(self):
        return max(1, self.area.get_scale_factor())

    def sheet(self):
        """The palette of the page area: the page colour of the current theme and the backdrop behind it."""
        t = RC.resolve_theme(self.theme_name, datetime.datetime.now().hour)
        return t, RC.paper_color(t)

    def shown_on(self):
        return self.get_mapped()

    # ------------------------------------------------------------------ the library: files, covers
    def refresh(self):
        """Look for PDFs in a worker thread, then show them and fill in titles and covers that are missing."""
        if self.scanning:
            self.rescan = True                      # a look is under way: look once more when it is done
            return
        self.scanning = True
        self.last_scan = time.time()
        self.cfg = BK.config()

        def work():
            files = BK.scan(self.cfg["dirs"])
            GLib.idle_add(self.set_files, files)
        threading.Thread(target=work, daemon=True).start()

    def set_files(self, files):
        self.scanning = False
        self.files = files
        if self.rescan:
            self.rescan = False
            self.refresh()
        self.index.forget_missing(files)
        self.entries = BK.library(self.store, self.index, files)
        self.rearrange()
        self.index_missing()
        self.area.queue_draw()
        return False

    def rearrange(self):
        self.shown = BK.arrange(self.entries, self.query, self.status, self.sort)
        self.lib_y = max(0.0, min(self.lib_y, max(0.0, self.lib_h - (self.H - LIB_HEAD))))

    def update_entry(self, bid):
        """After reading: progress and 'last opened' of one book."""
        for e in self.entries:
            if e["id"] == bid:
                b = self.store.book(bid)
                e.update(progress=self.store.progress(bid), status=self.store.status(bid), page=b["page"],
                         opened=b["opened"], marks=len(b["bookmarks"]) + len(b["highlights"]) + len(b["notes"]))

    def index_missing(self):
        """Titles, page counts and covers for books the library has not opened yet (one worker thread, one book at a
        time, in the background; the library already shows file names meanwhile)."""
        if self.indexing or not RC.Poppler:
            return
        todo = [f for f in self.files if not self.index.get(f) or not os.path.exists(self.cover_path(f))]
        if not todo:
            return
        self.indexing = True

        def work():
            for f in todo:
                try:
                    title, author, pages = RC.job_cover(f["path"], self.cover_path(f))
                except Exception as exc:                                         # a broken file must not stop the rest
                    print(f"kiln: books: cannot read {os.path.basename(f['path'])}: {exc}", flush=True)
                    title, author, pages = "", "", 0
                GLib.idle_add(self.indexed, f, title, author, pages)
            GLib.idle_add(self.index_done)
        threading.Thread(target=work, daemon=True).start()

    def indexed(self, f, title, author, pages):
        self.index.put(f, title, author, pages)
        bid = BK.book_id(f["path"], f["size"])
        self.covers.pop(bid, None)
        for e in self.entries:
            if e["id"] == bid:
                e.update(title=self.index.get(f)["title"] or e["title"], author=self.index.get(f)["author"],
                         pages=pages or e["pages"], known=True)
                self.store.book(bid)["pages"] = pages or self.store.book(bid)["pages"]
        self.area.queue_draw()
        return False

    def index_done(self):
        self.indexing = False
        self.index.save()
        self.rearrange()
        self.area.queue_draw()
        return False

    def cover_path(self, f):
        return f"{BK.COVERS}/{BK.book_id(f['path'], f['size'])}.png"

    def cover(self, e):
        """The cover as a surface, or None while there is none yet."""
        if e["id"] in self.covers:
            return self.covers[e["id"]]
        path = f"{BK.COVERS}/{e['id']}.png"
        surf = None
        if os.path.exists(path) and self.loaded_now < 4:
            try:
                surf = cairo.ImageSurface.create_from_png(path)
            except (cairo.Error, OSError):
                surf = False
            self.loaded_now += 1
            self.covers[e["id"]] = surf
        return surf or None

    def add_files(self, paths):
        """Copy PDFs into the first books folder (in a thread; they can be big), then look again."""
        def work():
            added, bad = 0, []
            for p in paths:
                try:
                    BK.add_book(p, self.cfg["dirs"][0])
                    added += 1
                except (ValueError, OSError) as exc:
                    bad.append(f"{os.path.basename(p)}: {exc}")
            GLib.idle_add(self.added, added, bad)
        threading.Thread(target=work, daemon=True).start()

    def added(self, n, bad):
        self.toast((f"Added {n} book{'s' if n != 1 else ''}" if n else "") + ((" · " if n and bad else "") + bad[0] if bad else ""))
        self.refresh()
        return False

    def on_drop(self, _w, ctx, _x, _y, data, _info, time_):
        paths = []
        for uri in data.get_uris() or []:
            u = urllib.parse.urlparse(uri)
            if u.scheme == "file" and u.path.lower().endswith(".pdf"):
                paths.append(urllib.parse.unquote(u.path))
        ctx.finish(bool(paths), False, time_)
        if paths:
            self.add_files(paths)
        else:
            self.toast("Drop PDF files here to add them")

    def choose_files(self):
        dlg = Gtk.FileChooserDialog(title="Add books", transient_for=self.get_toplevel(), action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
        dlg.set_select_multiple(True)
        flt = Gtk.FileFilter()
        flt.set_name("PDF files")
        flt.add_pattern("*.pdf")
        flt.add_pattern("*.PDF")
        dlg.add_filter(flt)

        def done(d, resp):
            paths = d.get_filenames() if resp == Gtk.ResponseType.OK else []
            d.destroy()
            if paths:
                self.add_files(paths)
        dlg.connect("response", done)
        dlg.show()

    # ------------------------------------------------------------------ drawing: the frame
    def on_draw(self, _w, cr):
        self.cr = cr
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        self.hits = []
        self.loaded_now = 0
        K.card_bg(cr, 0, 0, self.W, self.H, 20)
        try:
            if not RC.Poppler:
                self.draw_missing(cr)
            elif self.mode == "reader":
                self.draw_reader(cr)
            else:
                self.draw_library(cr)
            self.draw_toast(cr)
        except Exception as exc:                          # a broken view must not take the desktop down
            print(f"kiln: books view: {exc!r}", flush=True)
        return False

    def draw_missing(self, cr):
        K.text(cr, "Books needs Poppler", 36, 90, 20, K.C["text"], bold=True)
        K.text(cr, "Install it once, then restart Kiln:", 36, 122, 14, K.C["dim"])
        K.text(cr, "sudo apt install gir1.2-poppler-0.18", 36, 156, 14, K.C["acc"], mono=True)

    def draw_toast(self, cr):
        if self.toast_text and time.time() < self.toast_until:
            w = K.text(cr, self.toast_text, 0, -100, 13, K.C["text"]) + 36
            x, y = (self.W - w) / 2, self.H - BOTTOM - 62 if self.mode == "reader" else self.H - 70
            K.rrect(cr, x, y, w, 36, 18)
            K.set_rgba(cr, K.C["menu"], 0.97)
            cr.fill_preserve()
            K.set_rgba(cr, K.C["border"], 0.5)
            cr.set_line_width(1)
            cr.stroke()
            K.text(cr, self.toast_text, self.W / 2, y + 23, 13, K.C["text"], align="c")

    def button(self, cr, key, label, x, y, w, h=32, primary=False, on=False, size=12.5):
        self.region(key, x, y, w, h, hot=False)
        hot = self.hover == key
        K.rrect(cr, x, y, w, h, h / 2)
        if primary:
            K.set_rgba(cr, K.C["acc"], 1 if hot else 0.9)
        elif on:
            K.set_rgba(cr, K.C["acc"], 0.22)
        else:
            K.set_rgba(cr, K.C["ink"], 0.14 if hot else 0.07)
        cr.fill()
        K.text(cr, label, x + w / 2, y + h / 2 + size * 0.36, size, K.C["on_acc"] if primary else K.C["text"],
               bold=primary or on, align="c")

    # ------------------------------------------------------------------ drawing: the library
    def draw_library(self, cr):
        C = K.C
        K.glyph(cr, "folder", 26, 20, 22, C["acc2"])
        K.text(cr, "Books", 58, 38, 20, C["text"], bold=True)
        n = len(self.entries)
        K.text(cr, f"{n} book{'s' if n != 1 else ''}" if n else "", 58 + 78, 38, 12.5, C["dim"])
        right = self.W - 24
        right -= 118
        self.button(cr, ("add",), "+  Add book", right, 12, 118, primary=True)
        right -= 40
        self.region(("refresh",), right, 14, 30, 28)
        K.glyph(cr, "restart", right + 7, 21, 16, C["text"] if self.hover == ("refresh",) else C["dim"])
        # filters, sort and the filter box
        x = 26
        for key, label in STATUS_LABEL:
            count = sum(1 for e in self.entries if key == "all" or e["status"] == key)
            w = K.text(cr, label, 0, -100, 12.5, C["text"]) + 28 + len(str(count)) * 7
            self.button(cr, ("status", key), f"{label}  {count}", x, 62, w, 30, on=self.status == key, size=12.5)
            x += w + 6
        bx = self.W - 24 - 260
        self.region(("filterbox",), bx, 60, 260, 34, hot=False)
        K.rrect(cr, bx, 60, 260, 34, 17)
        K.set_rgba(cr, C["ink"], 0.07)
        cr.fill()
        K.glyph(cr, "search", bx + 12, 68, 16, C["dim"])
        K.text(cr, self.query or "Filter  ( / )", bx + 38, 82, 13, C["text"] if self.query else C["dim"], maxw=200)
        sx = bx - 12
        label = f"Sort: {SORT_LABEL[self.sort]}"
        w = K.text(cr, label, 0, -100, 12.5, C["text"]) + 28
        self.button(cr, ("sort",), label, sx - w, 62, w, 30)
        cr.save()
        cr.rectangle(0, LIB_HEAD, self.W, self.H - LIB_HEAD - 8)
        cr.clip()
        y = LIB_HEAD - self.lib_y
        if not self.entries:
            self.draw_empty(cr, y)
        else:
            y = self.draw_stats(cr, y)
            y = self.draw_continue(cr, y)
            y = self.draw_grid(cr, y)
        self.lib_h = y + self.lib_y - LIB_HEAD + 20
        cr.restore()
        self.bar(cr, self.W - 10, LIB_HEAD, self.H - LIB_HEAD - 12, self.lib_y, self.lib_h)
        if self.entry_mode is None and self.hover is None:
            self.cursor(None)

    def bar(self, cr, x, y, h, off, total):
        """A thin scroll bar on the right edge."""
        if total <= h:
            return
        th = max(30, h * h / total)
        ty = y + (h - th) * (off / (total - h))
        K.rrect(cr, x - 3, ty, 4, th, 2)
        K.set_rgba(cr, K.C["ink"], 0.18)
        cr.fill()

    def draw_empty(self, cr, y):
        C = K.C
        K.text(cr, "No books yet", self.W / 2, y + 150, 22, C["text"], bold=True, align="c")
        folder = self.cfg["dirs"][0].replace(BK.HOME, "~")
        K.text(cr, f"Put PDF files in {folder}, drop them here, or press Add book.", self.W / 2, y + 184, 14, C["dim"], align="c")
        self.button(cr, ("add",), "+  Add book", self.W / 2 - 70, y + 214, 140, 38, primary=True, size=13.5)

    def draw_stats(self, cr, y):
        """Today's reading against your goal, the streak, pages, and the week."""
        C = K.C
        x0, w, h = 24, self.W - 48, 104
        K.rrect(cr, x0, y + 6, w, h, 16)
        K.set_rgba(cr, C["ink"], 0.05)
        cr.fill()
        secs, pages = self.store.today()
        goal = self.cfg["daily_goal_minutes"] * 60
        cx, cy, r = x0 + 58, y + 6 + h / 2, 34
        cr.set_line_width(7)
        K.set_rgba(cr, C["ink"], 0.1)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        frac = min(1.0, secs / goal)
        if frac > 0:
            K.set_rgba(cr, C["green"] if frac >= 1 else C["acc"], 1)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.arc(cx, cy, r, -math.pi / 2, -math.pi / 2 + 2 * math.pi * frac)
            cr.stroke()
            cr.set_line_cap(cairo.LINE_CAP_BUTT)
        cr.new_path()
        K.text(cr, f"{int(secs // 60)}", cx, cy + 5, 17, C["text"], bold=True, align="c")
        K.text(cr, "min", cx, cy + 20, 10, C["dim"], align="c")
        x = x0 + 120
        K.text(cr, "Today", x, y + 38, 12, C["dim"])
        K.text(cr, f"{BK.fmt_minutes(secs / 60)} of {self.cfg['daily_goal_minutes']}m", x, y + 62, 17, C["text"], bold=True)
        K.text(cr, "goal reached" if frac >= 1 else f"{BK.fmt_minutes((goal - secs) / 60)} to go", x, y + 84, 12,
               C["green"] if frac >= 1 else C["dim"])
        x += 190
        streak = self.store.streak()
        for label, val, sub in (("Streak", f"{streak}", "day" + ("s" if streak != 1 else "") + " in a row"),
                                ("Pages today", f"{pages}", "pages read"),
                                ("Finished", f"{self.store.finished_count()}", "books")):
            K.text(cr, label, x, y + 38, 12, C["dim"])
            K.text(cr, val, x, y + 66, 24, C["amber"] if label == "Streak" and streak else C["text"], bold=True)
            K.text(cr, sub, x, y + 86, 11.5, C["dim"])
            x += 150
        # the week: seven bars, today last
        week = self.store.week()
        bx, bw = max(x + 20, self.W - 24 - 240), 240
        if bx + bw < self.W - 24 + 1:
            top = max([s for _d, s in week] + [goal])
            for i, (d, s) in enumerate(week):
                px = bx + i * (bw / 7) + 6
                bh = max(3, 52 * s / top)
                K.rrect(cr, px, y + 6 + 20 + (52 - bh), 20, bh, 4)
                K.set_rgba(cr, C["acc"] if i == 6 else C["ink"], 0.9 if i == 6 else (0.28 if s else 0.1))
                cr.fill()
                K.text(cr, d.strftime("%a")[:2], px + 10, y + 6 + h - 12, 10, C["dim"], align="c")
        return y + h + 22

    def draw_continue(self, cr, y):
        C = K.C
        items = BK.continue_reading(self.entries, max(1, min(4, (self.W - 48) // 360)))
        if not items:
            return y
        K.text(cr, "Continue reading", 26, y + 18, 15, C["text"], bold=True)
        y += 32
        x, cw, ch = 24, 346, 128
        for e in items:
            self.region(("open", e["id"]), x, y, cw, ch, hot=False)
            K.rrect(cr, x, y, cw, ch, 14)
            K.set_rgba(cr, C["ink"], 0.10 if self.hover == ("open", e["id"]) else 0.06)
            cr.fill()
            self.draw_cover(cr, e, x + 12, y + 12, 78, 104)
            tx = x + 104
            lines = self.wrap(cr, e["title"], 15, cw - 120, 2, bold=True)
            for i, ln in enumerate(lines):
                K.text(cr, ln, tx, y + 30 + i * 19, 15, C["text"], bold=True, maxw=cw - 120)
            by = y + 30 + len(lines) * 19 + 6
            if e["author"]:
                K.text(cr, e["author"], tx, by + 6, 11.5, C["dim"], maxw=cw - 120)
            self.progress_bar(cr, tx, y + ch - 44, cw - 120, 5, e["progress"])
            left = self.store.minutes_left(e["id"])
            K.text(cr, f"page {e['page'] + 1} of {e['pages']}" if e["pages"] else f"page {e['page'] + 1}", tx, y + ch - 16, 11.5, C["dim"])
            K.text(cr, f"{e['progress'] * 100:.0f}%" + (f" · ~{BK.fmt_minutes(left)} left" if left else ""),
                   x + cw - 14, y + ch - 16, 11.5, C["acc"], align="r")
            x += cw + 14
        return y + ch + 24

    def draw_grid(self, cr, y):
        C = K.C
        K.text(cr, "Library" if self.status == "all" and not self.query else f"{len(self.shown)} found", 26, y + 18, 15,
               C["text"], bold=True)
        y += 34
        if not self.shown:
            K.text(cr, "Nothing matches.", 26, y + 30, 13.5, C["dim"])
            return y + 60
        cw = CARD_COVER_H * 3 // 4
        cols = max(2, (self.W - 48 + 18) // (cw + 18))
        gap = (self.W - 48 - cols * cw) / max(1, cols - 1) if cols > 1 else 18
        gap = max(18, min(gap, 60))
        cellh = CARD_COVER_H + 76
        for i, e in enumerate(self.shown):
            cx = 24 + (i % cols) * (cw + gap)
            cy = y + (i // cols) * cellh
            if cy > self.H or cy + cellh < LIB_HEAD:
                self.hits.append(((cx, max(cy, LIB_HEAD), cw, cellh - 8), ("open", e["id"])))
                continue
            self.region(("open", e["id"]), cx - 4, cy - 4, cw + 8, cellh - 6)
            self.draw_cover(cr, e, cx, cy, cw, CARD_COVER_H)
            for j, ln in enumerate(self.wrap(cr, e["title"], 12.5, cw, 2, bold=True)):
                K.text(cr, ln, cx, cy + CARD_COVER_H + 20 + j * 16, 12.5, C["text"], bold=True, maxw=cw)
            sub = {"finished": "Finished", "unread": f"{e['pages']} pages" if e["pages"] else ""}.get(
                e["status"], f"{e['progress'] * 100:.0f}% · page {e['page'] + 1}")
            K.text(cr, sub, cx, cy + CARD_COVER_H + 56, 11, C["green"] if e["status"] == "finished" else C["dim"], maxw=cw)
            if e["status"] == "reading":
                self.progress_bar(cr, cx, cy + CARD_COVER_H - 5, cw, 5, e["progress"], inside=True)
            if e["marks"]:
                K.pill(cr, cx + cw - 6, cy + 22, f"{e['marks']}", 10, C["amber"], align="r")
        rows = (len(self.shown) + cols - 1) // cols
        return y + rows * cellh

    def wrap(self, cr, s, size, width, lines, bold=False):
        """Break a title into at most `lines` lines that fit `width` (the last one is cut with …)."""
        words, out, cur = str(s).split(), [], ""
        for wd in words:
            trial = (cur + " " + wd).strip()
            if cur and K.text(cr, trial, 0, -100, size, K.C["text"], bold=bold) > width:
                out.append(cur)
                cur = wd
            else:
                cur = trial
        out.append(cur)
        if len(out) > lines:
            out = out[:lines - 1] + [" ".join(out[lines - 1:])]
        return out

    def progress_bar(self, cr, x, y, w, h, frac, inside=False):
        K.rrect(cr, x, y, w, h, h / 2)
        K.set_rgba(cr, "000000" if inside else K.C["ink"], 0.45 if inside else 0.12)
        cr.fill()
        if frac > 0:
            K.rrect(cr, x, y, max(h, w * frac), h, h / 2)
            K.set_rgba(cr, K.C["acc"], 1)
            cr.fill()

    def draw_cover(self, cr, e, x, y, w, h):
        surf = self.cover(e)
        K.rrect(cr, x, y, w, h, 7)
        if surf:
            cr.save()
            cr.clip_preserve()
            cr.translate(x, y)
            cr.scale(w / surf.get_width(), h / surf.get_height())
            cr.set_source_surface(surf, 0, 0)
            cr.paint()
            cr.restore()
            cr.new_path()
        else:
            seed = sum(ord(c) for c in e["title"])
            K.set_rgba(cr, hexmix(K.C["card"], K.PALETTE[seed % len(K.PALETTE)], 0.5), 1)
            cr.fill()
            K.text(cr, e["title"][:1].upper(), x + w / 2, y + h / 2 + 12, 34, K.C["text"], bold=True, align="c", alpha=0.8)
        K.rrect(cr, x + 0.5, y + 0.5, w - 1, h - 1, 7)
        cr.set_line_width(1)
        K.set_rgba(cr, K.C["ink"], 0.18)
        cr.stroke()
        if e["status"] == "finished":
            K.rrect(cr, x + w - 26, y + 6, 20, 20, 10)
            K.set_rgba(cr, K.C["green"], 1)
            cr.fill()
            K.glyph(cr, "check", x + w - 22, y + 10, 12, K.C["card"])

    # ------------------------------------------------------------------ the reader: state
    def reset_reader(self):
        if getattr(self, "worker", None):
            self.worker.close()
        self.worker = self.book = self.info = self.layout = None
        self.scale, self.fit, self.scroll_x, self.scroll_y, self.target_y = 1.0, True, 0.0, 0.0, 0.0
        self.cache, self.cache_bytes, self.pending, self.bad_pages = collections.OrderedDict(), 0, set(), set()
        self.texts, self.text_pending = collections.OrderedDict(), set()
        self.links, self.links_pending = {}, set()
        self.sel = self.hl = self.search = self.press = self.jump_back = None
        self.panel, self.panel_tab, self.panel_y = None, "toc", 0.0
        self.loading = self.failed = ""
        self.anim = self.pos_dirty = self.seeking = False
        self.ppp = 1.0
        self.last_theme = None

    def bid(self):
        return self.book["id"]

    def open_book(self, e):
        self.reset_reader()
        self.mode, self.book, self.loading = "reader", e, "Opening…"
        self.store.opened(e["id"])
        self.store.flush()
        self.worker = RC.PdfWorker(e["path"])
        bid = e["id"]
        self.worker.submit(-1, RC.job_info, self.bound(self.got_info, bid))
        self.tracker.stop()
        self.tracker.poke(time.time())
        self.area.grab_focus()
        self.toast("/ search · g page · b bookmark · t contents · n note · d theme · s two pages · Esc back", 6)
        self.area.queue_draw()

    def got_info(self, bid, res):
        if not self.book or self.bid() != bid:
            return False
        if isinstance(res, Exception):
            text = str(res)
            self.failed = "This PDF is protected by a password." if "assword" in text or "ncrypt" in text else \
                f"Cannot open this PDF ({text[:80]})"
            self.area.queue_draw()
            return False
        self.info, self.loading = res, ""
        b = self.store.book(bid)
        self.book["pages"] = res["pages"]
        self.store.set_position(bid, b["page"], b["frac"], pages=res["pages"])
        if res["title"] and not self.book.get("known"):
            self.book["title"] = BK.A.clean_text(res["title"], 160)
        z = b["zoom"]
        self.fit = z is None
        self.scale = max(MIN_SCALE, min(MAX_SCALE, self.fit_scale() if self.fit else float(z)))
        self.layout = self.new_layout(self.scale)
        self.scroll_y = self.target_y = self.clamp_y(self.layout.position(b["page"], b["frac"]) - PROBE)
        self.area.queue_draw()
        return False

    def close_book(self):
        if self.book and self.layout:
            self.save_position()
        bid = self.book["id"] if self.book else None
        self.store.flush()
        self.tracker.stop()
        self.close_entry()
        self.reset_reader()
        self.mode = "library"
        if bid:
            self.update_entry(bid)
        self.rearrange()
        self.refresh()
        self.area.queue_draw()

    def save_position(self):
        page, frac = self.layout.anchor(self.scroll_y + PROBE)
        was = self.store.book(self.bid())["finished"]
        self.store.set_position(self.bid(), page, frac, pages=self.info["pages"])
        self.store.set_zoom(self.bid(), None if self.fit else self.scale)
        if not was and self.store.book(self.bid())["finished"]:
            self.toast("You finished this book")
        self.pos_dirty = False

    # ------------------------------------------------------------------ the reader: geometry and scrolling
    def canvas(self):
        cw = self.W - 28 - (PANEL_W + 10 if self.panel else 0)
        return 14, TOP, cw, self.H - TOP - BOTTOM - 6

    def new_layout(self, scale):
        return RC.PageLayout(self.info["sizes"], scale, spread=self.spread)

    def fit_scale(self):
        """The zoom that fits the width: one page, or both pages of a spread."""
        _cx, _cy, cw, _ch = self.canvas()
        widest = max((w for w, _h in self.info["sizes"]), default=595)
        one = (cw - 2 * PAD - (RC.PageLayout.HGAP if self.spread else 0)) / (2 if self.spread else 1)
        return max(MIN_SCALE, min(MAX_SCALE, min(one, FIT_MAX_W) / widest))                 # (not huge on a wide screen)

    def clamp_y(self, y):
        _cx, _cy, _cw, ch = self.canvas()
        return max(0.0, min(y, max(0.0, self.layout.total - ch + 16)))

    def clamp_x(self):
        _cx, _cy, cw, _ch = self.canvas()
        self.scroll_x = max(0.0, min(self.scroll_x, max(0.0, self.layout.col_w + 2 * PAD - cw)))

    def cur_page(self):
        if not self.layout:
            return 0
        _cx, _cy, _cw, ch = self.canvas()
        return self.layout.page_at(self.scroll_y + ch * 0.33)

    def target_page(self):
        """The page we are scrolling to (the page in view once the scroll animation has finished)."""
        if not self.layout:
            return 0
        return self.layout.page_at(self.target_y + self.canvas()[3] * 0.33)

    def apply_zoom(self, scale, anchor=None, keep=True):
        """Set the zoom (None: fit the width), keeping the spot at `anchor` (pixels below the top of the page area)."""
        if not self.info:
            return
        ay = anchor or 0
        page, frac = self.layout.anchor(self.scroll_y + ay)
        if scale is None:
            scale = self.fit_scale()
        self.scale = max(MIN_SCALE, min(MAX_SCALE, scale))
        self.layout = self.new_layout(self.scale)
        self.scroll_y = self.target_y = self.clamp_y(self.layout.position(page, frac) - ay)
        self.clamp_x()
        self.pos_dirty = True
        self.area.queue_draw()

    def zoom_by(self, factor, anchor=None):
        if self.layout:
            self.fit = False
            self.apply_zoom(self.scale * factor, anchor)

    def zoom_fit(self):
        if self.layout:
            self.fit = True
            self.apply_zoom(None)

    def scroll_by(self, dy, smooth=True):
        if not self.layout:
            return
        self.tracker.poke(time.time())
        self.target_y = self.clamp_y(self.target_y + dy)
        self.pos_dirty = True
        if smooth and not K.LIGHT:
            if not self.anim:
                self.anim = True
                self.area.add_tick_callback(self.step)
        else:
            self.scroll_y = self.target_y
            self.area.queue_draw()

    def scroll_to(self, y, smooth=True):
        self.scroll_by(self.clamp_y(y) - self.target_y, smooth)

    def step(self, _w, _clock):
        d = self.target_y - self.scroll_y
        if abs(d) < 0.6 or not self.layout:
            self.scroll_y, self.anim = self.target_y, False
            self.area.queue_draw()
            return False
        self.scroll_y += d * 0.3
        self.area.queue_draw()
        return True

    def go_page(self, p, remember=True):
        if self.layout:
            p = max(0, min(self.info["pages"] - 1, p))
            if remember:
                self.jump_back = self.scroll_y
            self.scroll_to(self.layout.tops[p] - 6)

    # ------------------------------------------------------------------ the reader: pages, text, links
    def want_pages(self, vis):
        theme, _paper = self.sheet()
        self.ppp = round(self.scale * self.dpr(), 2)
        n = self.info["pages"]
        for p in list(vis) + [q for q in (vis.start - 1, vis.stop, vis.stop + 1) if 0 <= q < n]:
            key = (p, self.ppp, theme)
            if key in self.cache:
                self.cache.move_to_end(key)
            elif key not in self.pending and p not in self.bad_pages:
                self.pending.add(key)
                self.worker.submit(RC.PdfWorker.VISIBLE if p in vis else RC.PdfWorker.AHEAD,
                                   lambda d, k=key: self.render_job(d, k), self.bound(self.rendered, key))
        for p in vis:
            if p not in self.texts and p not in self.text_pending:
                self.text_pending.add(p)
                self.worker.submit(RC.PdfWorker.TEXT, lambda d, q=p: RC.job_text(d, q), self.bound(self.got_text, p))
            if p not in self.links and p not in self.links_pending:
                self.links_pending.add(p)
                self.worker.submit(RC.PdfWorker.TEXT, lambda d, q=p: RC.job_links(d, q), self.bound(self.got_links, p))

    def bound(self, fn, *args):
        """A callback for a worker job that does nothing when another book has been opened since (a late answer must
        not end up in the wrong book)."""
        w = self.worker
        return lambda res: fn(*args, res) if self.worker is w else False

    def render_job(self, doc, key):
        if key[1] != self.ppp:                                    # zoomed again meanwhile: skip it
            return None
        return RC.job_render(doc, key[0], key[1], key[2])

    def rendered(self, key, res):
        self.pending.discard(key)
        if res is None:
            self.area.queue_draw()
            return False
        if isinstance(res, Exception):
            self.bad_pages.add(key[0])
        elif self.book:
            self.cache[key] = res
            self.cache_bytes += res.get_stride() * res.get_height()
            budget = (96 if K.LIGHT else 220) * 1024 * 1024
            while self.cache_bytes > budget and len(self.cache) > 3:
                _k, old = self.cache.popitem(last=False)
                self.cache_bytes -= old.get_stride() * old.get_height()
        self.area.queue_draw()
        return False

    def got_text(self, p, res):
        self.text_pending.discard(p)
        if not isinstance(res, Exception) and self.book:
            self.texts[p] = res
            while len(self.texts) > 60:
                self.texts.popitem(last=False)
        return False

    def got_links(self, p, res):
        self.links_pending.discard(p)
        self.links[p] = [] if isinstance(res, Exception) else res
        return False

    def page_surface(self, p, theme):
        exact = self.cache.get((p, self.ppp, theme))
        if exact:
            return exact
        for (q, _s, t), surf in self.cache.items():              # an older size of it, until the new one is ready
            if q == p and t == theme:
                return surf
        return None

    def page_box(self, p):
        """(x, y, w, h) of page p in widget coordinates."""
        cx, cy, cw, _ch = self.canvas()
        pw, ph = self.layout.width(p), self.layout.height(p)
        col = self.layout.col_w
        x0 = cx + (cw - col) / 2 if col + 2 * PAD <= cw else cx + PAD - self.scroll_x
        return x0 + self.layout.xoff[p], cy + self.layout.tops[p] - self.scroll_y, pw, ph

    def to_page(self, p, x, y):
        """Widget point -> point on page p in PDF points."""
        px, py, _pw, _ph = self.page_box(p)
        return (x - px) / self.scale, (y - py) / self.scale

    def page_under(self, x, y):
        if not self.layout:
            return None
        cx, cy, cw, ch = self.canvas()
        if not (cx <= x < cx + cw and cy <= y < cy + ch):
            return None
        for p in self.layout.visible(self.scroll_y + y - cy, self.scroll_y + y - cy):
            px, py, pw, ph = self.page_box(p)
            if px <= x < px + pw and py <= y < py + ph:
                return p
        return None

    # ------------------------------------------------------------------ the reader: drawing
    def draw_reader(self, cr):
        C = K.C
        cx, cy, cw, ch = self.canvas()
        theme, paper = self.sheet()
        K.rrect(cr, cx, cy, cw, ch, 12)
        K.set_rgba(cr, hexmix(C["card"], "000000", 0.4 if theme != "sepia" else 0.15), 0.9)
        cr.fill()
        if self.failed or not self.layout:
            K.text(cr, self.failed or self.loading, cx + cw / 2, cy + ch / 2, 16, C["red"] if self.failed else C["dim"], align="c")
        else:
            cr.save()
            K.rrect(cr, cx, cy, cw, ch, 12)
            cr.clip()
            vis = self.layout.visible(self.scroll_y, self.scroll_y + ch)
            self.want_pages(vis)
            for p in vis:
                self.draw_page(cr, p, theme, paper)
            cr.restore()
            self.draw_scrollbar(cr, cx + cw - 8, cy + 6, ch - 12)
        self.draw_topbar(cr)
        if self.layout:
            self.draw_bottombar(cr)
            if self.sel or self.hl:
                self.draw_selection_bar(cr)
        if self.panel:
            self.draw_panel(cr)
        if self.search:
            self.draw_search_count(cr)
        if theme != self.last_theme:
            self.last_theme = theme

    def draw_scrollbar(self, cr, x, y, h):
        if self.layout.total > h:
            frac = self.scroll_y / max(1.0, self.layout.total - h)
            th = max(36, h * h / self.layout.total)
            K.rrect(cr, x, y + (h - th) * frac, 4, th, 2)
            K.set_rgba(cr, K.C["ink"], 0.25)
            cr.fill()

    def draw_page(self, cr, p, theme, paper):
        x, y, w, h = self.page_box(p)
        cr.rectangle(x + 1, y + 3, w, h)
        cr.set_source_rgba(0, 0, 0, 0.35)
        cr.fill()
        surf = self.page_surface(p, theme)
        if surf:
            cr.save()
            cr.translate(x, y)
            cr.scale(w / surf.get_width(), h / surf.get_height())
            cr.set_source_surface(surf, 0, 0)
            cr.paint()
            cr.restore()
        else:
            K.set_rgba(cr, paper, 1)
            cr.rectangle(x, y, w, h)
            cr.fill()
            K.text(cr, "page not readable" if p in self.bad_pages else "…", x + w / 2, y + h / 2, 14, "888888", align="c")
        k = self.scale
        night = theme == "night"
        for hl in self.store.highlights(self.bid(), p):                                # saved highlights
            col = HL_COLORS.get(hl.get("color"), HL_COLORS["yellow"])
            chosen = self.hl and self.hl == (p, hl.get("id"))
            for r in hl.get("rects", []):
                K.set_rgba(cr, col, 0.5 if night else 0.42)
                cr.rectangle(x + r[0] * k, y + r[1] * k, (r[2] - r[0]) * k, (r[3] - r[1]) * k)
                cr.fill()
                if chosen:
                    cr.set_line_width(1.5)
                    K.set_rgba(cr, K.C["acc"], 1)
                    cr.rectangle(x + r[0] * k, y + r[1] * k, (r[2] - r[0]) * k, (r[3] - r[1]) * k)
                    cr.stroke()
            if hl.get("note") and hl.get("rects"):                                      # a note mark at the line end
                r = hl["rects"][0]
                K.rrect(cr, x + w - 22, y + r[1] * k, 14, 14, 4)
                K.set_rgba(cr, col, 1)
                cr.fill()
                cr.new_path()
        s = self.search
        if s and p in s["hits"]:
            for i, r in enumerate(s["hits"][p]):
                cur = s["cur"] == (p, i)
                K.set_rgba(cr, "ff8a3d" if cur else "ffd23d", 0.65 if cur else 0.45)
                cr.rectangle(x + r[0] * k - 1, y + r[1] * k, (r[2] - r[0]) * k + 2, (r[3] - r[1]) * k)
                cr.fill()
        if self.sel and self.sel["page"] == p:
            for r in self.sel["rects"]:
                K.set_rgba(cr, K.C["sel"], 0.45)
                cr.rectangle(x + r[0] * k, y + r[1] * k, (r[2] - r[0]) * k, (r[3] - r[1]) * k)
                cr.fill()
        if p in self.store.bookmarks(self.bid()):                                          # a ribbon
            cr.move_to(x + w - 40, y)
            cr.line_to(x + w - 16, y)
            cr.line_to(x + w - 16, y + 34)
            cr.line_to(x + w - 28, y + 26)
            cr.line_to(x + w - 40, y + 34)
            cr.close_path()
            K.set_rgba(cr, K.C["red"], 0.95)
            cr.fill()

    def icon_button(self, cr, key, x, kind, on=False, tip=""):
        self.region(key, x, 11, 34, 34)
        c = K.C["acc"] if on else (K.C["text"] if self.hover == key else K.C["dim"])
        cx, cy = x + 17, 28
        cr.new_path()
        if kind == "list":
            for i in (-5, 0, 5):
                K.rrect(cr, cx - 8, cy + i - 1, 16, 2, 1)
                K.set_rgba(cr, c, 1)
                cr.fill()
        elif kind == "star":
            self.draw_ribbon(cr, cx, cy, c, on)
        elif kind == "note":
            K.rrect(cr, cx - 8, cy - 8, 16, 14, 3)
            K.set_rgba(cr, c, 1)
            cr.set_line_width(1.6)
            cr.stroke()
            cr.move_to(cx - 3, cy + 5)
            cr.line_to(cx - 3, cy + 10)
            cr.line_to(cx + 3, cy + 5)
            cr.stroke()
        elif kind == "spread":
            for dx in (-9, 1):
                K.rrect(cr, cx + dx, cy - 7, 8, 14, 2)
                K.set_rgba(cr, c, 1)
                cr.set_line_width(1.6)
                cr.stroke()
        elif kind in ("search", "sun", "moon", "x"):
            K.glyph(cr, kind, cx - 9, cy - 9, 18, c)
        else:
            K.text(cr, kind, cx, cy + 6, 18, c, align="c")

    def draw_ribbon(self, cr, cx, cy, color, filled):
        cr.move_to(cx - 6, cy - 9)
        cr.line_to(cx + 6, cy - 9)
        cr.line_to(cx + 6, cy + 9)
        cr.line_to(cx, cy + 4)
        cr.line_to(cx - 6, cy + 9)
        cr.close_path()
        K.set_rgba(cr, color, 1)
        if filled:
            cr.fill()
        else:
            cr.set_line_width(1.6)
            cr.stroke()

    def draw_topbar(self, cr):
        C = K.C
        self.button(cr, ("back",), "‹  Library", 16, 12, 104)
        x = self.W - 16
        x -= 34
        self.icon_button(cr, ("panel", "toc"), x, "list", on=self.panel is not None)
        x -= 34
        marked = self.layout and self.cur_page() in self.store.bookmarks(self.bid())
        self.icon_button(cr, ("bookmark",), x, "star", on=bool(marked))
        x -= 34
        self.icon_button(cr, ("find",), x, "search", on=self.search is not None)
        x -= 34
        theme, _paper = self.sheet()
        self.icon_button(cr, ("theme",), x, "moon" if theme == "night" else "sun", on=theme != "light")
        x -= 34
        self.icon_button(cr, ("spread",), x, "spread", on=self.spread)
        x -= 12 + 34
        self.icon_button(cr, ("zoom", 1), x, "+")
        pct = f"{self.scale * 100:.0f}%" if not self.fit else "Fit"
        x -= 58
        self.region(("zoom", 0), x, 14, 54, 28)
        K.text(cr, pct, x + 27, 33, 12.5, C["text"] if self.hover == ("zoom", 0) else C["dim"], align="c")
        x -= 34
        self.icon_button(cr, ("zoom", -1), x, "−")
        x -= 12
        if self.layout:
            shown = self.layout.row_pages(self.cur_page())
            label = f"{shown[0] + 1}{'–' + str(shown[-1] + 1) if len(shown) > 1 else ''} / {self.info['pages']}"
            w = K.text(cr, label, 0, -100, 13, C["text"]) + 20
            x -= w
            self.region(("goto",), x, 14, w, 28)
            K.text(cr, label, x + w / 2, 33, 13, C["text"], align="c", bold=True)
        title = self.book["title"] if self.book else ""
        K.text(cr, title, 136, 34, 15, C["text"], bold=True, maxw=max(120, x - 150))

    def draw_bottombar(self, cr):
        C = K.C
        y = self.H - BOTTOM + 2
        p = self.cur_page()
        n = self.info["pages"]
        frac = min(1.0, (self.layout.anchor(self.scroll_y + self.canvas()[3] * 0.33)[0] + self.layout.anchor(self.scroll_y + self.canvas()[3] * 0.33)[1]) / n)
        K.text(cr, f"page {p + 1} of {n}  ·  {frac * 100:.0f}%", 28, y + 17, 12, C["dim"])
        left = self.store.minutes_left(self.bid())
        spent = self.store.book_secs(self.bid())
        right = (f"~{BK.fmt_minutes(left)} left  ·  " if left else "") + (f"{BK.fmt_minutes(spent / 60)} read" if spent >= 60 else "")
        K.text(cr, right, self.W - 28, y + 17, 12, C["dim"], align="r")
        tx0, tx1 = 230, self.W - 230
        self.track = (tx0, tx1)
        self.region(("seek",), tx0 - 6, y + 2, tx1 - tx0 + 12, 24, hot=False)
        K.rrect(cr, tx0, y + 11, tx1 - tx0, 4, 2)
        K.set_rgba(cr, C["ink"], 0.14)
        cr.fill()
        K.rrect(cr, tx0, y + 11, max(4, (tx1 - tx0) * frac), 4, 2)
        K.set_rgba(cr, C["acc"], 1)
        cr.fill()
        for mp in self.store.bookmarks(self.bid()):
            mx = tx0 + (tx1 - tx0) * mp / max(1, n)
            K.set_rgba(cr, C["red"], 1)
            cr.rectangle(mx - 1, y + 7, 2.5, 12)
            cr.fill()
        for hl in self.store.highlights(self.bid()):
            mx = tx0 + (tx1 - tx0) * hl.get("page", 0) / max(1, n)
            K.set_rgba(cr, HL_COLORS.get(hl.get("color"), "ffd54a"), 0.9)
            cr.rectangle(mx - 1, y + 9, 2, 8)
            cr.fill()
        kx = tx0 + (tx1 - tx0) * frac
        cr.arc(kx, y + 13, 6.5 if (self.hover == ("seek",) or self.seeking) else 5, 0, 2 * math.pi)
        K.set_rgba(cr, C["acc_hi"], 1)
        cr.fill()
        cr.new_path()

    def draw_search_count(self, cr):
        s = self.search
        x = (self.W - 360) / 2 + 360 + 12
        order = s["order"]
        if order:
            label = f"{order.index(s['cur']) + 1 if s['cur'] in order else 0} of {len(order)}" + ("" if s["done"] else " …")
        else:
            label = "No matches" if s["done"] else "Searching…"
        K.text(cr, label, x, 33, 12.5, K.C["dim"])
        self.button(cr, ("hit", -1), "↑", x + 90, 12, 30, 32, size=14)
        self.button(cr, ("hit", 1), "↓", x + 126, 12, 30, 32, size=14)

    # ---- the little bar over a selection or a chosen highlight
    def selection_anchor(self):
        """Where to put the bar: above the first line of the selection (or highlight), else below it."""
        if self.sel:
            p, rects = self.sel["page"], self.sel["rects"]
        else:
            p = self.hl[0]
            h = next((h for h in self.store.highlights(self.bid(), p) if h.get("id") == self.hl[1]), None)
            rects = h["rects"] if h else []
        if not rects:
            return None
        x, y, _w, _h = self.page_box(p)
        r = rects[0]
        return x + (r[0] + r[2]) / 2 * self.scale, y + r[1] * self.scale, y + rects[-1][3] * self.scale

    def draw_selection_bar(self, cr):
        a = self.selection_anchor()
        if not a:
            return
        ax, top, bottom = a
        items = [("copy", "Copy", 56)] + [("color", c, 28) for c in BK.COLORS] + [("note", "Note", 54)]
        if self.hl:
            items.append(("del", "Delete", 62))
        w = sum(i[2] for i in items) + 16
        x = max(20, min(ax - w / 2, self.W - w - 20))
        y = top - 50 if top - 50 > TOP + 6 else bottom + 10
        y = max(TOP + 6, min(y, self.H - BOTTOM - 50))
        K.rrect(cr, x, y, w, 40, 20)
        K.set_rgba(cr, K.C["menu"], 0.98)
        cr.fill_preserve()
        K.set_rgba(cr, K.C["border"], 0.6)
        cr.set_line_width(1)
        cr.stroke()
        ix = x + 8
        for kind, label, iw in items:
            key = ("tb", kind, label) if kind == "color" else ("tb", kind)
            self.region(key, ix, y + 5, iw, 30)
            if kind == "color":
                cr.arc(ix + 14, y + 20, 8.5, 0, 2 * math.pi)
                K.set_rgba(cr, HL_COLORS[label], 1)
                cr.fill()
                cr.new_path()
            else:
                K.text(cr, label, ix + iw / 2, y + 25, 12.5, K.C["red"] if kind == "del" else K.C["menu_fg"], align="c")
            ix += iw

    # ---- the side panel: contents, bookmarks, notes
    def panel_rows(self):
        """[(height, kind, data)] of the open tab."""
        rows = []
        if self.panel_tab == "toc":
            toc = self.info["outline"]
            here = max([i for i, t in enumerate(toc) if t[2] is not None and t[2] <= self.cur_page()], default=-1)
            rows = [(32, "toc", (i, t, i == here)) for i, t in enumerate(toc)]
        elif self.panel_tab == "marks":
            rows = [(36, "mark", p) for p in self.store.bookmarks(self.bid())]
        else:
            items = [(h["page"], 0, h) for h in self.store.highlights(self.bid())] + \
                    [(n["page"], 1, n) for n in self.store.notes(self.bid())]
            for page, kind, item in sorted(items, key=lambda r: (r[0], r[1], r[2].get("id", 0))):
                body = item["text"] if kind == 1 else (item.get("text") or "(highlight)")
                lines = min(3, 1 + len(body) // 34)
                extra = 1 + min(2, len(item.get("note", "")) // 34) if kind == 0 and item.get("note") else 0
                rows.append((26 + lines * 17 + extra * 17, "hl" if kind == 0 else "note", item))
        return rows

    def draw_panel(self, cr):
        C = K.C
        x0, y0, w, h = self.W - 14 - PANEL_W, TOP, PANEL_W, self.H - TOP - BOTTOM - 6
        K.rrect(cr, x0, y0, w, h, 12)
        K.set_rgba(cr, C["ink"], 0.05)
        cr.fill()
        tabs = (("toc", "Contents", len(self.info["outline"])), ("marks", "Bookmarks", len(self.store.bookmarks(self.bid()))),
                ("notes", "Notes", len(self.store.highlights(self.bid())) + len(self.store.notes(self.bid()))))
        tx = x0 + 10
        for key, label, count in tabs:
            tw = K.text(cr, label, 0, -100, 12, C["text"]) + 22 + (len(str(count)) * 7 if count else 0)
            self.button(cr, ("tab", key), label + (f" {count}" if count else ""), tx, y0 + 8, tw, 28, on=self.panel_tab == key, size=12)
            tx += tw + 4
        top, bottom = y0 + 46, y0 + h - 8
        cr.save()
        cr.rectangle(x0, top, w, bottom - top)
        cr.clip()
        y = top - self.panel_y
        total = 0
        rows = self.panel_rows()
        if not rows:
            msg = {"toc": "This book has no table of contents.", "marks": "No bookmarks yet.  Press  b  on a page.",
                   "notes": "Select text to highlight it,\nor press  n  to add a note to the page."}[self.panel_tab]
            for i, ln in enumerate(msg.split("\n")):
                K.text(cr, ln, x0 + w / 2, top + 40 + i * 20, 12.5, C["dim"], align="c")
        for ht, kind, data in rows:
            total += ht
            if y + ht >= top and y <= bottom:
                self.draw_panel_row(cr, x0 + 8, y, w - 16, ht, kind, data)
            y += ht
        cr.restore()
        self.panel_total = total
        self.panel_view = (top, bottom)
        self.panel_rect = (x0, y0, w, h)
        self.panel_y = max(0.0, min(self.panel_y, max(0.0, total - (bottom - top))))
        if self.panel_tab == "notes":
            self.button(cr, ("addnote",), "+ Note on this page", x0 + w - 150, bottom - 30, 140, 26, size=11.5)

    def draw_panel_row(self, cr, x, y, w, h, kind, data):
        C = K.C
        if kind == "toc":
            i, (level, title, page), here = data
            self.region(("go", page), x, y, w, h - 2)
            if here:
                K.rrect(cr, x, y, w, h - 2, 8)
                K.set_rgba(cr, C["acc"], 0.16)
                cr.fill()
            K.text(cr, title, x + 8 + min(level, 4) * 14, y + 21, 13, C["acc2"] if here else C["text"], bold=here,
                   maxw=w - 70 - min(level, 4) * 14)
            if page is not None:
                K.text(cr, str(page + 1), x + w - 8, y + 21, 11.5, C["dim"], align="r")
        elif kind == "mark":
            self.region(("go", data), x, y, w, h - 2)
            self.draw_ribbon(cr, x + 16, y + 17, C["red"], True)
            K.text(cr, f"Page {data + 1}", x + 34, y + 23, 13, C["text"])
            self.region(("unmark", data), x + w - 30, y + 4, 26, 26)
            K.glyph(cr, "x", x + w - 24, y + 10, 12, C["dim"])
        else:
            hl = kind == "hl"
            self.region(("go", data["page"], data["id"]) if hl else ("pnote", data["id"], data["page"]), x, y, w, h - 4)
            if hl:
                cr.arc(x + 12, y + 19, 5, 0, 2 * math.pi)
                K.set_rgba(cr, HL_COLORS.get(data.get("color"), "ffd54a"), 1)
                cr.fill()
                cr.new_path()
            else:
                K.rrect(cr, x + 7, y + 13, 10, 9, 2)
                K.set_rgba(cr, C["acc"], 1)
                cr.fill()
            K.text(cr, f"Page {data['page'] + 1}", x + 26, y + 22, 11.5, C["dim"])
            body = data.get("text") or "(highlight)"
            cy = y + 40
            for ln in self.wrap(cr, body, 12.5, w - 50, 3):
                K.text(cr, ln, x + 26, cy, 12.5, C["text"], maxw=w - 50)
                cy += 17
            if hl and data.get("note"):
                for ln in self.wrap(cr, data["note"], 12, w - 50, 3):
                    K.text(cr, ln, x + 26, cy, 12, C["acc2"], maxw=w - 50)
                    cy += 17
            self.region(("delete", kind, data["id"]), x + w - 30, y + 4, 26, 26)
            K.glyph(cr, "x", x + w - 24, y + 10, 12, C["dim"])

    # ------------------------------------------------------------------ events
    def activate(self, key, ev):
        k = key[0]
        if k == "add":
            self.choose_files()
        elif k == "refresh":
            self.refresh()
            self.toast("Looking for new books…", 1.5)
        elif k == "status":
            self.status, self.lib_y = key[1], 0.0
            self.rearrange()
        elif k == "sort":
            order = list(SORT_LABEL)
            self.sort = order[(order.index(self.sort) + 1) % len(order)]
            self.rearrange()
        elif k == "filterbox":
            self.open_entry("filter", self.query)
        elif k == "open":
            e = next((e for e in self.entries if e["id"] == key[1]), None)
            if e:
                self.open_book(e)
        elif k == "back":
            self.close_book()
        elif k == "panel":
            self.toggle_panel(self.panel_tab)
        elif k == "tab":
            self.panel_tab, self.panel_y = key[1], 0.0
        elif k == "bookmark":
            self.toggle_bookmark()
        elif k == "find":
            self.open_entry("search", self.search["q"] if self.search else "")
        elif k == "theme":
            self.cycle_theme()
        elif k == "spread":
            self.toggle_spread()
        elif k == "zoom":
            if key[1] == 0:
                self.zoom_fit()
            else:
                self.zoom_by(ZOOM_STEP ** key[1], self.canvas()[3] / 2)
        elif k == "goto":
            self.open_entry("goto", "")
        elif k == "seek":
            self.seeking = True
            self.seek(ev.x)
        elif k == "go":
            self.go_page(key[1]) if key[1] is not None else None
            if len(key) > 2 and key[2] is not None:
                self.hl, self.sel = (key[1], key[2]), None
        elif k == "pnote":
            self.go_page(key[2])
            self.edit_page_note(key[1])
        elif k == "unmark":
            self.store.toggle_bookmark(self.bid(), key[1])
            self.store.flush()
        elif k == "delete":
            if key[1] == "hl":
                self.store.remove_highlight(self.bid(), key[2])
                self.hl = None if self.hl and self.hl[1] == key[2] else self.hl
            else:
                self.store.remove_note(self.bid(), key[2])
            self.store.flush()
        elif k == "addnote":
            self.edit_page_note(None)
        elif k == "hit":
            self.step_hit(key[1])
        elif k == "tb":
            self.toolbar(key)
        self.area.queue_draw()

    def on_press(self, _w, ev):
        self.area.grab_focus()
        self.tracker.poke(time.time())
        key = self.hit(ev.x, ev.y)
        if key:
            if ev.button == 3 and key[0] == "open":
                self.context_menu(key[1], ev)
            elif ev.button == 1 and ev.type == Gdk.EventType.BUTTON_PRESS:
                self.activate(key, ev)
            return True
        if self.mode == "reader" and ev.button == 1:
            self.begin_press(ev)
        return False

    def begin_press(self, ev):
        p = self.page_under(ev.x, ev.y)
        self.press = None
        if p is None:
            self.sel = self.hl = None
            self.area.queue_draw()
            return
        pt = self.to_page(p, ev.x, ev.y)
        text, chars = self.texts.get(p, ("", []))
        idx = RC.char_at(chars, *pt) if chars else None
        if ev.type == Gdk.EventType._2BUTTON_PRESS and idx is not None:
            self.select_word(p, idx)
            return
        self.press = dict(x=ev.x, y=ev.y, page=p, idx=idx, moved=False, link=self.link_at(p, pt), hl=self.highlight_at(p, pt))

    def on_motion(self, _w, ev):
        if self.seeking:
            self.seek(ev.x)
            return True
        pr = self.press
        if pr and (ev.state & Gdk.ModifierType.BUTTON1_MASK):
            if not pr["moved"] and max(abs(ev.x - pr["x"]), abs(ev.y - pr["y"])) > 4:
                pr["moved"] = True
            if pr["moved"] and pr["idx"] is not None and pr["page"] in self.texts:
                _text, chars = self.texts[pr["page"]]
                j = RC.char_at(chars, *self.to_page(pr["page"], ev.x, ev.y))
                if j is not None:
                    self.update_sel(pr["page"], pr["idx"], j)
                    self.area.queue_draw()
            return True
        key = self.hit(ev.x, ev.y)
        self.set_hover(key)
        if key:
            self.cursor("pointer")
        elif self.mode == "reader":
            p = self.page_under(ev.x, ev.y)
            link = p is not None and self.link_at(p, self.to_page(p, ev.x, ev.y))
            self.cursor("pointer" if link else "text" if p is not None else None)
        else:
            self.cursor(None)
        return False

    def on_release(self, _w, ev):
        if self.seeking:
            self.seeking = False
            self.area.queue_draw()
            return True
        pr, self.press = self.press, None
        if not pr or ev.button != 1:
            return False
        if not pr["moved"]:
            if pr["link"]:
                self.follow_link(pr["link"])
            elif pr["hl"]:
                self.hl, self.sel = (pr["page"], pr["hl"]), None
            else:
                self.sel = self.hl = None
        elif self.sel and not self.sel["text"].strip():
            self.sel = None
        self.area.queue_draw()
        return True

    def on_scroll(self, _w, ev):
        self.tracker.poke(time.time())
        if ev.direction == Gdk.ScrollDirection.SMOOTH:
            dx, dy = ev.delta_x * 55, ev.delta_y * 55
        else:
            dx, dy = {Gdk.ScrollDirection.UP: (0, -90), Gdk.ScrollDirection.DOWN: (0, 90),
                      Gdk.ScrollDirection.LEFT: (-90, 0), Gdk.ScrollDirection.RIGHT: (90, 0)}.get(ev.direction, (0, 0))
        if self.mode == "library":
            self.lib_y = max(0.0, min(self.lib_y + dy, max(0.0, self.lib_h - (self.H - LIB_HEAD))))
        elif self.panel and getattr(self, "panel_rect", None) and self.panel_rect[0] <= ev.x:
            top, bottom = self.panel_view
            self.panel_y = max(0.0, min(self.panel_y + dy, max(0.0, self.panel_total - (bottom - top))))
        elif self.layout:
            if ev.state & Gdk.ModifierType.CONTROL_MASK:
                if dy:
                    self.zoom_by(ZOOM_STEP ** (-1 if dy > 0 else 1), ev.y - self.canvas()[1])
            elif ev.state & Gdk.ModifierType.SHIFT_MASK:
                self.scroll_x += dy or dx
                self.clamp_x()
            else:
                if dx:
                    self.scroll_x += dx
                    self.clamp_x()
                if dy:
                    self.scroll_by(dy)
        self.area.queue_draw()
        return True

    def on_key(self, _w, ev):
        key, state = ev.keyval, ev.state
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        ch = chr(Gdk.keyval_to_unicode(key)) if Gdk.keyval_to_unicode(key) else ""
        self.tracker.poke(time.time())
        if ctrl and key in (Gdk.KEY_f, Gdk.KEY_F):
            self.open_entry("search" if self.mode == "reader" else "filter", self.search["q"] if self.mode == "reader" and self.search else self.query)
            return True
        if self.mode == "library":
            if key == Gdk.KEY_Escape and self.query:
                self.query = ""
                self.rearrange()
            elif ch == "/" or (ch.isprintable() and ch.strip() and not ctrl and key < 0xff00):
                self.open_entry("filter", "" if ch == "/" else self.query + ch)
            else:
                return False
            self.area.queue_draw()
            return True
        if not self.layout:                                     # still opening (or failed): only a way back
            if key == Gdk.KEY_Escape:
                self.close_book()
                return True
            return False
        _cx, _cy, _cw, chh = self.canvas()
        if key == Gdk.KEY_Escape:
            if self.sel or self.hl:
                self.sel = self.hl = None
            elif self.search:
                self.search = None
            elif self.panel:
                self.toggle_panel(self.panel_tab)
            else:
                self.close_book()
        elif key in (Gdk.KEY_Down, Gdk.KEY_j):
            self.scroll_by(70)
        elif key in (Gdk.KEY_Up, Gdk.KEY_k):
            self.scroll_by(-70)
        elif key in (Gdk.KEY_Page_Down, Gdk.KEY_space) and not shift:
            self.scroll_by(chh * 0.9)
        elif key in (Gdk.KEY_Page_Up, Gdk.KEY_space):
            self.scroll_by(-chh * 0.9)
        elif key == Gdk.KEY_Home:
            self.go_page(0, remember=False)
        elif key == Gdk.KEY_End:
            self.go_page(self.info["pages"] - 1, remember=False)
        elif key in (Gdk.KEY_Right, Gdk.KEY_l):
            self.go_page(self.layout.step(self.target_page(), 1), remember=False)
        elif key in (Gdk.KEY_Left, Gdk.KEY_h):
            self.go_page(self.layout.step(self.target_page(), -1), remember=False)
        elif ch == "s":
            self.toggle_spread()
        elif ch in ("+", "="):
            self.zoom_by(ZOOM_STEP, chh / 2)
        elif ch in ("-", "_"):
            self.zoom_by(1 / ZOOM_STEP, chh / 2)
        elif ch == "0":
            self.zoom_fit()
        elif ctrl and key in (Gdk.KEY_c, Gdk.KEY_C):
            self.toolbar(("tb", "copy"))
        elif key == Gdk.KEY_BackSpace and self.jump_back is not None:
            y, self.jump_back = self.jump_back, None
            self.scroll_to(y)
        elif ch == "/":
            self.open_entry("search", self.search["q"] if self.search else "")
        elif ch == "g":
            self.open_entry("goto", "")
        elif ch == "b":
            self.toggle_bookmark()
        elif ch == "t":
            self.toggle_panel("toc")
        elif ch == "m":
            self.toggle_panel("marks")
        elif ch == "n":
            self.edit_page_note(None)
        elif ch == "d":
            self.cycle_theme()
        elif key == Gdk.KEY_F3 or (ch in ("]",)):
            self.step_hit(-1 if shift else 1)
        else:
            return False
        self.area.queue_draw()
        return True

    # ---- the little text box over the view
    def entry_position(self, _overlay, widget, alloc):
        if widget is not self.entry:
            return False
        if self.entry_mode == "filter":
            x, y, w = self.W - 24 - 260 + 34, 62, 216
        elif self.entry_mode == "goto":
            x, y, w = (self.W - 160) / 2, 11, 160
        else:
            x, y, w = (self.W - 360) / 2, 11, 360
        alloc.x, alloc.y, alloc.width, alloc.height = int(x), int(y), int(w), 34 if self.entry_mode != "filter" else 30
        return True

    def open_entry(self, mode, text=""):
        self.entry_mode = mode
        self.entry.set_placeholder_text({"filter": "Title or author", "search": "Search the book  (Enter = next)",
                                         "goto": f"Page 1–{self.info['pages']}" if self.info else "Page"}[mode])
        self.entry.set_text(text)
        self.entry.show()
        self.queue_resize()
        self.entry.grab_focus()
        self.entry.set_position(-1)

    def close_entry(self):
        self.entry_mode = None
        self.entry.hide()
        self.area.grab_focus()
        self.area.queue_draw()

    def on_entry_changed(self, entry):
        if self.entry_mode == "filter":
            self.query, self.lib_y = entry.get_text(), 0.0
            self.rearrange()
            self.area.queue_draw()

    def on_entry_activate(self, entry):
        text = entry.get_text().strip()
        mode = self.entry_mode
        if mode == "search":
            if self.search and self.search["q"] == text:
                self.step_hit(1)
            else:
                self.start_search(text)
        elif mode == "goto":
            try:
                self.go_page(int(text) - 1)
                self.close_entry()
            except ValueError:
                self.toast(f"Type a page number from 1 to {self.info['pages']}")
        else:
            self.close_entry()

    def on_entry_key(self, _entry, ev):
        if ev.keyval == Gdk.KEY_Escape:
            if self.entry_mode == "filter":
                self.query = ""
                self.rearrange()
            self.close_entry()
            return True
        if ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and ev.state & Gdk.ModifierType.SHIFT_MASK and self.entry_mode == "search":
            self.step_hit(-1)
            return True
        return False

    # ------------------------------------------------------------------ reader actions
    def toggle_panel(self, tab):
        """Open the side panel on `tab`, switch to it when another tab is open, or close it when it is already showing."""
        if self.panel and self.panel_tab == tab:
            self.panel = None
        else:
            self.panel, self.panel_tab, self.panel_y = tab, tab, 0.0
        if self.layout and self.fit:
            self.apply_zoom(None)
        self.area.queue_draw()

    def toggle_bookmark(self):
        if self.layout:
            p = self.cur_page()
            added = self.store.toggle_bookmark(self.bid(), p)
            self.store.flush()
            self.toast(f"Bookmarked page {p + 1}" if added else f"Bookmark removed from page {p + 1}", 2)

    def toggle_spread(self):
        """Two pages side by side (like an open book) or one page at a time; the place in the book stays."""
        if not self.layout:
            return
        self.spread = not self.spread
        self.store.set_pref("spread", self.spread)
        self.store.flush()
        page, frac = self.layout.anchor(self.scroll_y + PROBE)
        self.scale = max(MIN_SCALE, min(MAX_SCALE, self.fit_scale() if self.fit else self.scale))
        self.layout = self.new_layout(self.scale)
        self.scroll_y = self.target_y = self.clamp_y(self.layout.position(page, frac) - PROBE)
        self.clamp_x()
        self.pos_dirty = True
        self.toast("Two pages side by side" if self.spread else "One page at a time", 2)
        self.area.queue_draw()

    def cycle_theme(self):
        order = list(BK.THEMES)
        self.theme_name = order[(order.index(self.theme_name) + 1) % len(order)]
        self.store.set_pref("theme", self.theme_name)
        self.store.flush()
        self.toast(f"{THEME_LABEL[self.theme_name]} pages" + (" (dark at night)" if self.theme_name == "auto" else ""), 2)

    def seek(self, x):
        if not self.layout:
            return
        tx0, tx1 = self.track
        frac = max(0.0, min(1.0, (x - tx0) / (tx1 - tx0)))
        self.go_page(int(frac * self.info["pages"]), remember=False)

    def link_at(self, p, pt):
        for (x1, y1, x2, y2), target in self.links.get(p, []):
            if x1 <= pt[0] <= x2 and y1 <= pt[1] <= y2:
                return target
        return None

    def highlight_at(self, p, pt):
        for h in self.store.highlights(self.bid(), p):
            for x1, y1, x2, y2 in h.get("rects", []):
                if x1 <= pt[0] <= x2 and y1 <= pt[1] <= y2:
                    return h.get("id")
        return None

    def follow_link(self, target):
        kind, dest = target
        if kind == "page":
            self.go_page(dest)
            self.toast(f"Page {dest + 1}  ·  Backspace goes back", 3)
        elif urllib.parse.urlparse(dest).scheme in ("http", "https", "mailto"):
            self.toast("Opening " + BK.A.clean_text(dest, 60), 3)
            K.run(["xdg-open", dest])
        else:
            self.toast("This kind of link is not opened", 3)

    def update_sel(self, p, a, b):
        text, chars = self.texts[p]
        lo, hi = min(a, b), max(a, b)
        rects = RC.selection_rects(chars, text, lo, hi)
        self.sel = dict(page=p, a=a, b=b, rects=rects, text=" ".join(text[lo:hi + 1].split())) if rects else None
        self.hl = None

    def select_word(self, p, idx):
        text, _chars = self.texts[p]
        lo = hi = idx
        while lo > 0 and not text[lo - 1].isspace():
            lo -= 1
        while hi + 1 < len(text) and not text[hi + 1].isspace():
            hi += 1
        self.update_sel(p, lo, hi)
        self.area.queue_draw()

    def toolbar(self, key):
        kind = key[1]
        bid = self.bid()
        if kind == "copy":
            text = self.sel["text"] if self.sel else self.hl_text()
            if text:
                Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
                self.toast("Copied", 1.5)
            return
        if kind == "color":
            if self.sel:
                hid = self.store.add_highlight(bid, self.sel["page"], self.sel["rects"], self.sel["text"], key[2])
                self.hl, self.sel = (self.sel_page(), hid), None
            elif self.hl:
                self.store.set_highlight_color(bid, self.hl[1], key[2])
        elif kind == "note":
            if self.sel:
                hid = self.store.add_highlight(bid, self.sel["page"], self.sel["rects"], self.sel["text"], "yellow")
                self.hl, self.sel = (self.sel_page(), hid), None
            if self.hl:
                self.edit_highlight_note(*self.hl)
        elif kind == "del" and self.hl:
            self.store.remove_highlight(bid, self.hl[1])
            self.hl = None
        self.store.flush()

    def sel_page(self):
        return self.sel["page"] if self.sel else (self.hl[0] if self.hl else 0)

    def hl_text(self):
        h = next((h for h in self.store.highlights(self.bid(), self.hl[0]) if h.get("id") == self.hl[1]), None) if self.hl else None
        return h.get("text", "") if h else ""

    # ---- notes
    def edit_text(self, title, initial, on_save, on_delete=None):
        dlg = Gtk.Dialog(title=title, transient_for=self.get_toplevel(), modal=True)
        dlg.set_default_size(460, 220)
        tv = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD, top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        tv.get_buffer().set_text(initial)
        tv.get_style_context().add_class("kiln-notes")
        sc = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN)
        sc.set_min_content_height(150)
        sc.add(tv)
        sc.set_margin_start(14)
        sc.set_margin_end(14)
        sc.set_margin_top(14)
        sc.set_margin_bottom(6)
        dlg.get_content_area().pack_start(sc, True, True, 0)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        if on_delete:
            dlg.add_button("Delete", Gtk.ResponseType.REJECT)
        dlg.add_button("Save", Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)

        def done(d, resp):
            buf = tv.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            d.destroy()
            if resp == Gtk.ResponseType.OK:
                on_save(text)
            elif resp == Gtk.ResponseType.REJECT and on_delete:
                on_delete()
            self.store.flush()
            self.area.grab_focus()
            self.area.queue_draw()
        dlg.connect("response", done)
        dlg.show_all()
        tv.grab_focus()

    def edit_highlight_note(self, page, hid):
        h = next((h for h in self.store.highlights(self.bid(), page) if h.get("id") == hid), None)
        if h:
            self.edit_text(f"Note · page {page + 1}", h.get("note", ""),
                           lambda t: self.store.set_highlight_note(self.bid(), hid, t))

    def edit_page_note(self, nid):
        if not self.layout:
            return
        page = self.cur_page()
        if nid is not None:
            n = next((n for n in self.store.notes(self.bid()) if n.get("id") == nid), None)
            if n:
                self.edit_text(f"Note · page {n['page'] + 1}", n["text"], lambda t: self.store.edit_note(self.bid(), nid, t),
                               lambda: self.store.remove_note(self.bid(), nid))
            return
        self.edit_text(f"Note · page {page + 1}", "", lambda t: self.store.add_note(self.bid(), page, t))

    # ---- search
    def start_search(self, q):
        if not q or not self.layout:
            self.search = None
            self.area.queue_draw()
            return
        tok = object()
        self.search = dict(q=q, hits={}, order=[], cur=None, done=False, token=tok, start=self.cur_page(), step=0)
        self.search_next(tok)
        self.area.queue_draw()

    def search_next(self, tok):
        s = self.search
        if not s or s["token"] is not tok:
            return
        n = self.info["pages"]
        if s["step"] >= n:
            s["done"] = True
            self.area.queue_draw()
            return
        p = (s["start"] + s["step"]) % n
        s["step"] += 1
        q = s["q"]
        self.worker.submit(RC.PdfWorker.SEARCH, lambda d: RC.job_find(d, p, q), self.bound(self.found, tok, p))

    def found(self, tok, p, res):
        s = self.search
        if not s or s["token"] is not tok:
            return False
        if not isinstance(res, Exception) and res:
            s["hits"][p] = res
            for i in range(len(res)):
                if len(s["order"]) < 3000:
                    bisect.insort(s["order"], (p, i))
            if s["cur"] is None:
                s["cur"] = (p, 0)
                self.goto_hit(s["cur"])
        self.search_next(tok)
        self.area.queue_draw()
        return False

    def step_hit(self, d):
        s = self.search
        if not s or not s["order"]:
            return
        i = s["order"].index(s["cur"]) if s["cur"] in s["order"] else -1
        s["cur"] = s["order"][(i + d) % len(s["order"])]
        self.goto_hit(s["cur"])
        self.area.queue_draw()

    def goto_hit(self, key):
        p, i = key
        box = self.search["hits"][p][i]
        _cx, _cy, _cw, ch = self.canvas()
        self.jump_back = self.scroll_y
        self.scroll_to(self.layout.tops[p] + box[1] * self.scale - ch * 0.3)

    # ---- the library's menu
    def context_menu(self, bid, ev):
        e = next((e for e in self.entries if e["id"] == bid), None)
        if not e:
            return
        menu = Gtk.Menu()

        def item(label, fn):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda *_: fn())
            menu.append(mi)

        def mark(done):
            (self.store.mark_finished if done else self.store.mark_unread)(bid)
            self.store.flush()
            self.update_entry(bid)
            self.rearrange()
            self.area.queue_draw()

        def export():
            try:
                self.toast("Saved " + os.path.basename(BK.write_export(e, self.store)), 5)
            except OSError as exc:
                self.toast(f"Cannot save the notes ({exc.strerror})")
        item("Open", lambda: self.open_book(e))
        item("Mark as unread" if e["status"] == "finished" else "Mark as finished", lambda: mark(e["status"] != "finished"))
        if e["marks"]:
            item("Export highlights and notes", export)
        item("Show in folder", lambda: K.run(["xdg-open", os.path.dirname(e["path"])]))
        menu.show_all()
        menu.popup_at_pointer(ev)

    # ---- the clock
    def tick(self):
        now = time.time()
        if not self.get_mapped():
            self.tracker.stop()
            return True
        if self.mode == "reader" and self.layout:
            self.tracker.tick(now, self.bid(), self.cur_page())
            if self.pos_dirty:
                self.save_position()
            if self.tracker.break_due(now, self.cfg["eye_break_minutes"]):
                self.eye_break()
            self.store.flush()
            if self.theme_name == "auto" and self.sheet()[0] != self.last_theme:
                self.area.queue_draw()
        else:
            self.tracker.stop()
            if now - self.last_scan > 90:
                self.refresh()
        return True

    def eye_break(self):
        self.toast("Rest your eyes: look at something far away for 20 seconds", 20)
        if K.ACC.notifications_on("books"):
            threading.Thread(target=K.ACC.notify, daemon=True,
                             args=("Eye break", "You have been reading for a while. Look at something far away for 20 seconds.")).start()
