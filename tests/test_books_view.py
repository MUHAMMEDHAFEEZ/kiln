"""The Books mode with real Poppler and synthetic mouse and key events: library, opening, scrolling, search, links,
selecting text, highlights, bookmarks, themes and reading time. Skipped without a display or Poppler."""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "share")
os.environ.setdefault("CANBERRA_DRIVER", "null")
sys.path.insert(0, os.path.dirname(__file__))
from test_reader_core import make_pdf  # noqa: E402


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display")
class BooksViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        os.makedirs(f"{t}/conf")
        os.makedirs(f"{t}/cache")
        os.environ.update(KILN_CONFIG=f"{t}/conf", KILN_CACHE=f"{t}/cache")
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("Gdk", "3.0")
            from gi.repository import Gdk, Gtk
            sys.path.insert(0, ROOT)
            import app
            import books as BK
            import reader_core as RC
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"no GTK here ({e})")
        if Gdk.Screen.get_default() is None or not RC.Poppler:
            raise unittest.SkipTest("no screen or no Poppler")
        cls.Gdk, cls.Gtk, cls.app, cls.BK, cls.RC = Gdk, Gtk, app, BK, RC
        cls.saved = {n: getattr(BK, n) for n in ("CONF", "CACHE", "DATA_FILE", "INDEX_FILE", "COVERS")}
        BK.CONF, BK.CACHE = f"{t}/conf", f"{t}/cache"
        BK.DATA_FILE, BK.INDEX_FILE, BK.COVERS = f"{t}/conf/books.json", f"{t}/cache/books-index.json", f"{t}/cache/covers"
        cls.books_dir = f"{t}/Books"
        os.makedirs(cls.books_dir)
        make_pdf(f"{cls.books_dir}/Big Book.pdf", pages=12)
        make_pdf(f"{cls.books_dir}/short.pdf", pages=3, link=False)
        with open(f"{t}/conf/config.json", "w") as f:
            json.dump({"books": {"dirs": [cls.books_dir], "theme": "light", "eye_break_minutes": 0, "view": "single"}}, f)
        app.RD.bind(app)
        cls.view = app.RD.BooksView(None)
        cls.view.set_size(1500, 800)
        cls.win = Gtk.Window()
        cls.win.add(cls.view)
        cls.win.show_all()
        cls.pump_until(lambda: len(cls.view.entries) == 2 and not cls.view.indexing, 15)

    @classmethod
    def tearDownClass(cls):
        cls.view.reset_reader()
        cls.win.destroy()
        for n, v in cls.saved.items():
            setattr(cls.BK, n, v)
        cls.tmp.cleanup()

    @classmethod
    def pump_until(cls, until, seconds=8):
        end = time.time() + seconds
        while time.time() < end and not until():
            while cls.Gtk.events_pending():
                cls.Gtk.main_iteration()
            time.sleep(0.02)
        return until()

    def setUp(self):
        v = self.view
        if v.mode == "reader":
            v.close_book()
        for name in os.listdir(self.books_dir):                      # a test that failed halfway leaves nothing behind
            if name not in ("Big Book.pdf", "short.pdf"):
                os.remove(f"{self.books_dir}/{name}")
        if len(v.entries) != 2:
            v.scanning = False
            v.refresh()
            self.pump_until(lambda: len(v.entries) == 2 and not v.indexing, 10)
        for e in v.entries:                                          # every book starts unread, fit to the width
            v.store.book(e["id"]).update(page=0, frac=0.0, zoom=None, finished=False, opened=0, furthest=0,
                                         bookmarks=[], highlights=[], notes=[])
            v.update_entry(e["id"])
        v.panel, v.search, v.sel, v.hl = None, None, None, None
        v.theme_name = "light"
        v.set_view("single", save=False)
        self.app.LIGHT = False                                        # (page turning is animated)
        v.query, v.status, v.sort = "", "all", "recent"
        v.rearrange()

    # ---- helpers
    def draw(self):
        """Draw the view once; a drawing error is caught and printed by the view, so look for it."""
        import cairo
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.view.W, self.view.H)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.view.on_draw(None, cairo.Context(surf))
        self.assertNotIn("kiln: books view", out.getvalue())
        return surf

    def entry(self, title):
        return next(e for e in self.view.entries if e["title"] == title)

    def open(self, title="Test Book", page=None):
        v = self.view
        v.open_book(self.entry(title))
        self.assertTrue(self.pump_until(lambda: v.layout is not None), "the book never opened")
        self.settle()
        return v

    def settle(self):
        v = self.view
        self.draw()
        vis = v.layout.rows[v.row] if v.paged else v.layout.visible(v.scroll_y, v.scroll_y + v.canvas()[3])
        self.pump_until(lambda: all(v.page_surface(p, v.sheet()[0]) is not None and p in v.texts and p in v.links for p in vis))
        self.draw()

    def key(self, name, state=0):
        e = self.Gdk.EventKey()
        e.type, e.keyval, e.state = self.Gdk.EventType.KEY_PRESS, getattr(self.Gdk, "KEY_" + name), state
        return self.view.on_key(None, e)

    def mouse(self, kind, x, y, button=1, state=0):
        if kind == "move":
            e = self.Gdk.EventMotion()
            e.type, e.x, e.y, e.state = self.Gdk.EventType.MOTION_NOTIFY, x, y, state
            return self.view.on_motion(None, e)
        e = self.Gdk.EventButton()
        e.type = self.Gdk.EventType.BUTTON_PRESS if kind == "press" else self.Gdk.EventType.BUTTON_RELEASE
        e.x, e.y, e.button = x, y, button
        return (self.view.on_press if kind == "press" else self.view.on_release)(None, e)

    def click_region(self, key):
        r = next(r for r, k in self.view.hits if k == key)
        x, y = r[0] + r[2] / 2, r[1] + r[3] / 2
        self.mouse("press", x, y)
        self.mouse("release", x, y)

    def point_on(self, p, pt_x, pt_y):
        bx, by, _w, _h = self.view.page_box(p)
        return bx + pt_x * self.view.scale, by + pt_y * self.view.scale

    # ---- the library
    def test_library_shows_both_books_with_titles_and_covers(self):
        v = self.view
        self.assertEqual(sorted(e["title"] for e in v.entries), ["Test Book", "Test Book"])      # both PDFs have that title
        self.assertEqual(sorted(e["pages"] for e in v.entries), [3, 12])
        self.assertTrue(all(os.path.exists(self.BK.cover_file(e['id'], 'auto')) for e in v.entries))
        self.draw()
        self.assertTrue(any(k[0] == "open" for _r, k in v.hits))

    def test_a_chosen_cover_picture_replaces_the_automatic_one(self):
        import cairo
        v, e = self.view, self.entry("Test Book")
        pic = f"{self.tmp.name}/mine.png"
        s = cairo.ImageSurface(cairo.FORMAT_RGB24, 60, 80)
        cr = cairo.Context(s)
        cr.set_source_rgb(1, 0, 0)
        cr.paint()
        s.write_to_png(pic)
        self.addCleanup(lambda: v.reset_cover(e))
        automatic = v.cover(e)
        v.set_cover(e, pic)
        mine = v.cover(e)
        self.assertEqual((mine.get_width(), mine.get_height()), (60, 80))
        self.assertIsNot(mine, automatic)
        self.draw()
        v.set_cover(e, f"{self.tmp.name}/missing.png")                  # a bad file is refused with a message
        self.assertIn("Cannot use it", v.toast_text)
        self.assertEqual(v.cover(e).get_width(), 60)
        v.reset_cover(e)
        self.assertNotEqual(v.cover(e).get_width(), 60)

    def test_filter_status_and_sort_buttons(self):
        v = self.view
        self.draw()
        self.click_region(("status", "finished"))
        self.assertEqual(v.shown, [])
        self.click_region(("status", "all"))
        self.assertEqual(len(v.shown), 2)
        self.click_region(("sort",))
        self.assertEqual(v.sort, "title")
        v.query = "zzz"
        v.rearrange()
        self.assertEqual(v.shown, [])
        self.draw()

    def test_dropping_a_pdf_adds_it(self):
        src = f"{self.tmp.name}/dropped.pdf"
        make_pdf(src, pages=2, link=False)
        v = self.view
        v.add_files([src])
        self.assertTrue(self.pump_until(lambda: len(v.entries) == 3 and not v.indexing, 10))
        self.assertTrue(os.path.exists(f"{self.books_dir}/dropped.pdf"))
        os.remove(f"{self.books_dir}/dropped.pdf")
        v.refresh()
        self.assertTrue(self.pump_until(lambda: len(v.entries) == 2, 10))

    def test_a_book_you_added_can_be_moved_to_the_trash(self):
        src = f"{self.tmp.name}/trash-me.pdf"
        make_pdf(src, pages=2, link=False)
        v = self.view
        v.add_files([src])
        self.assertTrue(self.pump_until(lambda: len(v.entries) == 3 and not v.indexing, 10))
        e = next(x for x in v.entries if x["path"].endswith("trash-me.pdf"))
        trashed = []
        saved = self.BK.trash_file
        self.BK.trash_file = lambda path: (trashed.append(path), os.remove(path))[0]        # no real Trash in a test
        try:
            v.remove(e)
        finally:
            self.BK.trash_file = saved
        self.assertEqual(trashed, [f"{self.books_dir}/trash-me.pdf"])
        self.assertEqual(len(v.entries), 2)                                    # gone from the shelf at once
        self.assertTrue(self.pump_until(lambda: len(v.entries) == 2 and not v.scanning, 10))
        self.assertNotIn(e["id"], v.store.books)

    # ---- reading
    def test_open_scroll_zoom_and_remember_the_place(self):
        v = self.open()
        self.assertTrue(v.fit)
        self.settle()
        self.assertIsNotNone(v.page_surface(0, v.sheet()[0]))
        fit = v.scale
        self.key("Page_Down")
        self.pump_until(lambda: not v.anim, 3)
        self.assertGreater(v.scroll_y, 100)
        self.key("End")
        self.pump_until(lambda: not v.anim, 5)
        self.assertEqual(v.cur_page(), 11)
        self.key("Home")
        self.pump_until(lambda: not v.anim, 5)
        self.assertEqual(v.cur_page(), 0)
        self.key("Right")
        self.key("Right")
        self.pump_until(lambda: not v.anim, 5)
        self.assertEqual(v.cur_page(), 2)
        self.key("plus")
        self.assertGreater(v.scale, fit)
        self.assertFalse(v.fit)
        self.key("0")
        self.assertTrue(v.fit)
        self.assertAlmostEqual(v.scale, fit, places=2)
        v.tick()                                                     # saves the position
        v.close_book()
        e = next(e for e in v.entries if e["pages"] == 12)
        self.assertEqual(e["status"], "reading")
        self.assertEqual(self.view.store.book(e["id"])["page"], 2)
        v.open_book(e)
        self.assertTrue(self.pump_until(lambda: v.layout is not None))
        self.assertEqual(v.layout.anchor(v.scroll_y)[0], 2)           # it opens where it was left

    def test_zoom_with_the_wheel_keeps_the_spot_under_the_pointer(self):
        v = self.open()
        self.key("Right")
        self.pump_until(lambda: not v.anim, 3)
        cx, cy, cw, ch = v.canvas()
        before = v.layout.anchor(v.scroll_y + 300)
        e = self.Gdk.EventScroll()
        e.type, e.direction, e.x, e.y, e.state = self.Gdk.EventType.SCROLL, self.Gdk.ScrollDirection.UP, cx + 300, cy + 300, \
            self.Gdk.ModifierType.CONTROL_MASK
        v.on_scroll(None, e)
        after = v.layout.anchor(v.scroll_y + 300)
        self.assertEqual(before[0], after[0])
        self.assertAlmostEqual(before[1], after[1], places=2)
        self.assertFalse(v.fit)
        self.draw()

    def test_the_views_cycle_and_keep_the_place(self):
        v = self.open()
        v.apply_zoom(0.8)
        v.fit = False
        v.go_page(2, remember=False)
        self.pump_until(lambda: not v.anim, 5)
        self.addCleanup(lambda: v.set_view("single", save=False))
        self.key("s")                                                  # one page -> two pages, scrolling
        self.assertEqual((v.view, v.spread, v.paged), ("spread", True, False))
        self.assertEqual(v.layout.rows[:3], [[0], [1, 2], [3, 4]])
        self.assertEqual(v.layout.anchor(v.scroll_y + 24)[0], 1)       # page 3 is in the row that starts with page 2
        self.settle()
        x1, y1, w1, _h1 = v.page_box(1)
        x2, y2, _w2, _h2 = v.page_box(2)
        self.assertAlmostEqual(y1, y2)
        self.assertAlmostEqual(x2 - (x1 + w1), v.layout.hgap)
        self.assertGreater(v.page_box(0)[0], x1)                       # the cover is the right-hand page
        self.assertEqual({v.page_under(x1 + 5, y1 + 150), v.page_under(x2 + 5, y2 + 150)}, {1, 2})
        self.key("Right")
        self.pump_until(lambda: not v.anim, 5)
        self.assertEqual(v.cur_page(), 3)                              # a whole spread at a time
        self.key("s")                                                  # -> the open book
        self.assertEqual((v.view, v.paged, v.fit), ("book", True, True))
        self.assertEqual(v.layout.rows[v.row], [3, 4])                 # still on the pages we were reading
        self.assertEqual(v.layout.hgap, 2)
        self.key("s")                                                  # -> one page again
        self.assertEqual((v.view, v.paged), ("single", False))
        self.assertEqual(v.layout.anchor(v.scroll_y + 24)[0], 3)
        self.assertEqual(self.BK.Store(self.BK.DATA_FILE).pref("view", "x"), "single")

    def book(self):
        v = self.open()
        v.set_view("book", save=False)
        v.apply_zoom(None)
        v.close_book()
        v.open_book(next(e for e in v.entries if e["pages"] == 12))
        self.assertTrue(self.pump_until(lambda: v.layout is not None))
        self.settle()
        self.addCleanup(lambda: v.set_view("single", save=False))
        return v

    def turned(self, v, row, seconds=5):
        self.assertTrue(self.pump_until(lambda: v.flip is None and v.row == row, seconds), f"row {v.row}, flip {v.flip}")
        self.draw()

    def test_the_open_book_fits_the_spread_and_starts_on_the_cover(self):
        v = self.book()
        self.assertTrue(v.paged)
        self.assertEqual((v.row, v.layout.rows[0]), (0, [0]))
        cx, cy, cw, ch = v.canvas()
        x0, y0, w0, h0 = v.page_box(0)
        self.assertGreaterEqual(y0, cy)
        self.assertLessEqual(y0 + h0, cy + ch)                          # the whole page is in view
        self.assertGreaterEqual(x0, cx + cw / 2 - 1)                    # the cover is the right-hand page
        self.assertEqual(v.layout.slots(0), (None, 0))

    def test_arrow_keys_turn_the_page_with_an_animation_and_a_second_press_waits(self):
        v = self.book()
        self.key("Right")
        self.assertIsNotNone(v.flip)                                   # the page is turning
        self.assertEqual((v.flip["a"], v.flip["b"], v.flip["dir"]), (0, 1, 1))
        self.pump_until(lambda: v.flip and v.flip["t"] > 0.3, 3)
        self.assertEqual(v.row, 0)                                     # not there until it has turned
        self.draw()                                                    # a frame in the middle of the turn
        self.turned(v, 1)
        self.assertEqual(v.cur_page(), 1)
        self.key("Right")
        self.key("Right")                                              # asked while turning: waits its turn
        self.turned(v, 3, 8)
        self.key("Left")
        self.turned(v, 2)
        self.key("space", self.Gdk.ModifierType.SHIFT_MASK)            # Shift+Space is back, like in the scrolling views
        self.turned(v, 1)

    def test_every_moment_of_a_turn_can_be_drawn_forwards_and_backwards(self):
        v = self.book()
        v.go_page(3, remember=False)
        self.pump_until(lambda: v.flip is None, 5)
        for d, a, b in ((1, v.row, v.row + 1), (-1, v.row, v.row - 1)):
            for t in (0.0, 0.1, 0.25, 0.49, 0.5, 0.51, 0.75, 0.99, 1.0):
                v.flip = dict(dir=d, a=a, b=b, t=t, drag=True, frm=0.0, to=1.0, start=0.0, ticking=False)
                self.draw()
        v.flip = dict(dir=-1, a=0, b=0, t=0.4, drag=True, frm=0.0, to=1.0, start=0.0, ticking=False)
        v.row = 0
        v.flip = dict(dir=1, a=0, b=1, t=0.3, drag=True, frm=0.0, to=1.0, start=0.0, ticking=False)   # from the cover
        self.draw()
        v.flip = None

    def test_the_ends_of_the_book_do_not_turn(self):
        v = self.book()
        self.key("Left")
        self.assertIsNone(v.flip)
        self.assertIn("first page", v.toast_text)
        v.go_page(11, remember=False)
        self.assertEqual(v.layout.rows[v.row], [11])
        self.key("Right")
        self.assertIsNone(v.flip)
        self.assertIn("last page", v.toast_text)
        self.assertEqual(v.layout.slots(v.row), (11, None))            # an odd last page sits on the left

    def test_clicking_the_edges_of_the_page_area_turns_the_page(self):
        v = self.book()
        cx, cy, cw, ch = v.canvas()
        self.mouse("press", cx + cw - 20, cy + ch / 2)
        self.mouse("release", cx + cw - 20, cy + ch / 2)               # the right edge: forward
        self.turned(v, 1)
        self.mouse("press", cx + 20, cy + ch / 2)
        self.mouse("release", cx + 20, cy + ch / 2)                    # the left edge: back
        self.turned(v, 0)
        self.mouse("press", cx + cw / 2, cy + ch / 2)
        self.mouse("release", cx + cw / 2, cy + ch / 2)                # the middle does not turn
        self.assertIsNone(v.flip)

    def test_dragging_a_page_edge_turns_it_with_the_pointer_or_lets_it_fall_back(self):
        v = self.book()
        cx, cy, cw, ch = v.canvas()
        left = self.Gdk.ModifierType.BUTTON1_MASK
        y = cy + ch / 2
        self.mouse("press", cx + cw - 20, y)
        self.mouse("move", cx + cw - 60, y, state=left)
        self.mouse("move", cx + cw - 300, y, state=left)
        self.assertTrue(v.flip["drag"])
        self.assertAlmostEqual(v.flip["t"], 280 / (cw * 0.45), places=2)    # the page follows the pointer
        self.draw()
        self.mouse("release", cx + cw - 300, y)                       # far enough: it turns on by itself
        self.turned(v, 1)
        self.mouse("press", cx + cw - 20, y)
        self.mouse("move", cx + cw - 50, y, state=left)
        self.mouse("move", cx + cw - 70, y, state=left)
        self.assertTrue(v.flip["t"] < 0.2)
        self.mouse("release", cx + cw - 70, y)                        # not far: it falls back
        self.pump_until(lambda: v.flip is None, 3)
        self.assertEqual(v.row, 1)
        self.mouse("press", cx + 20, y)                               # dragging the wrong way does nothing
        self.mouse("move", cx + 5, y, state=left)
        self.mouse("move", cx - 30, y, state=left)
        self.assertIsNone(v.flip)
        self.mouse("release", cx - 30, y)

    def test_the_wheel_turns_one_page_per_notch(self):
        v = self.book()
        e = self.Gdk.EventScroll()
        e.type, e.direction, e.x, e.y, e.state = self.Gdk.EventType.SCROLL, self.Gdk.ScrollDirection.DOWN, 500, 400, 0
        v.on_scroll(None, e)
        v.on_scroll(None, e)                                          # straight away: the page is still turning
        self.turned(v, 1)
        self.assertEqual(v.row, 1)
        self.assertIsNone(v.queued)
        e.direction = self.Gdk.ScrollDirection.UP
        v.last_turn = 0
        v.on_scroll(None, e)
        self.turned(v, 0)

    def test_the_book_remembers_the_spread_and_jumps_far_without_turning(self):
        v = self.book()
        self.key("Right")
        self.turned(v, 1)
        self.key("Right")
        self.turned(v, 2)
        v.tick()
        v.close_book()
        e = next(e for e in v.entries if e["pages"] == 12)
        self.assertEqual(v.store.book(e["id"])["page"], 3)            # the left page of the spread it stopped at
        v.open_book(e)
        self.assertTrue(self.pump_until(lambda: v.layout is not None))
        self.assertEqual(v.row, 2)
        v.go_page(10)                                                 # far away: straight there
        self.assertIsNone(v.flip)
        self.assertEqual(v.layout.rows[v.row], [9, 10])
        self.key("BackSpace")
        self.assertEqual(v.layout.rows[v.row], [3, 4])                # back to where we were

    def test_book_view_search_zoom_bookmark_and_panel(self):
        v = self.book()
        scale = v.scale
        self.key("plus")
        self.assertEqual(v.scale, scale)                              # zoom is for the scrolling views
        self.assertIn("scrolling", v.toast_text)
        v.start_search("NEEDLE6")
        self.assertTrue(self.pump_until(lambda: v.search["done"], 10))
        self.pump_until(lambda: v.flip is None and v.layout.rows[v.row] == [5, 6], 5)
        self.assertEqual(v.layout.rows[v.row], [5, 6])                # the spread that holds the match
        self.draw()
        self.key("b")
        self.assertEqual(v.store.bookmarks(v.bid()), [5])
        self.key("b")
        self.assertEqual(v.store.bookmarks(v.bid()), [])
        self.key("t")                                                 # the panel narrows the page area: refit, same spread
        self.assertEqual(v.layout.rows[v.row], [5, 6])
        self.assertTrue(v.paged)
        self.draw()
        self.key("Escape")                                            # first the search goes, then the panel
        self.assertIsNone(v.search)
        self.assertIsNotNone(v.panel)
        self.key("Escape")
        self.assertIsNone(v.panel)

    def test_text_can_still_be_selected_in_the_open_book(self):
        v = self.book()
        self.key("Right")
        self.turned(v, 1)
        self.settle()
        text, chars = v.texts[1]
        x1, y1, _x2, y2 = chars[0]
        end = chars[3]
        sx, sy = self.point_on(1, x1 + 1, (y1 + y2) / 2)
        ex, ey = self.point_on(1, end[0] + 2, (end[1] + end[3]) / 2)
        self.mouse("press", sx, sy)
        self.mouse("move", ex, ey, state=self.Gdk.ModifierType.BUTTON1_MASK)
        self.mouse("release", ex, ey)
        self.assertEqual(v.sel["text"], "Page")

    def test_contents_panel_and_jumping_from_it(self):
        v = self.open()
        self.key("t")
        self.assertEqual(v.panel, "toc")
        self.draw()
        self.assertEqual([t[1] for t in v.info["outline"]], ["Chapter 1", "Chapter 2", "Second", "Chapter 3"])
        self.click_region(("go", 8))
        self.pump_until(lambda: not v.anim, 5)
        self.assertEqual(v.cur_page(), 8)
        self.key("Escape")
        self.assertIsNone(v.panel)

    def test_search_finds_jumps_and_steps(self):
        v = self.open()
        v.open_entry("search", "")
        v.entry.set_text("NEEDLE5")
        v.entry.emit("activate")
        self.assertTrue(self.pump_until(lambda: v.search["done"], 10))
        self.assertEqual(len(v.search["order"]), 10)
        self.assertEqual(v.search["order"][0][0], 5)
        self.pump_until(lambda: not v.anim, 5)
        self.assertEqual(v.cur_page(), 5)
        first = v.search["cur"]
        v.entry.emit("activate")                                      # Enter again: the next one
        self.assertNotEqual(v.search["cur"], first)
        v.step_hit(-1)
        self.assertEqual(v.search["cur"], first)
        self.settle()
        v.close_entry()
        self.key("Escape")
        self.assertIsNone(v.search)

    def test_search_without_a_match_says_so(self):
        v = self.open("Test Book")
        v.start_search("zzzzzz")
        self.assertTrue(self.pump_until(lambda: v.search["done"], 10))
        self.assertEqual(v.search["order"], [])
        self.draw()

    def test_a_link_goes_to_its_page_and_backspace_returns(self):
        v = self.open()
        v.fit = False
        v.apply_zoom(1.0)                                              # a page of 600 px: its link is on screen
        self.settle()
        links = v.links[0]
        box = next(b for b, k in links if k == ("page", 2))
        x, y = self.point_on(0, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        self.mouse("press", x, y)
        self.mouse("release", x, y)
        self.pump_until(lambda: not v.anim, 5)
        self.assertEqual(v.cur_page(), 2)
        self.key("BackSpace")
        self.pump_until(lambda: not v.anim, 5)
        self.assertEqual(v.cur_page(), 0)

    def test_select_text_highlight_note_and_delete(self):
        v = self.open()
        self.settle()
        text, chars = v.texts[0]
        x1, y1, _x2, y2 = chars[0]                                    # "P" of "Page 1 heading"
        end = chars[3]                                                # the "e" of "Page"
        sx, sy = self.point_on(0, x1 + 1, (y1 + y2) / 2)
        ex, ey = self.point_on(0, end[0] + 2, (end[1] + end[3]) / 2)
        self.mouse("press", sx, sy)
        self.mouse("move", (sx + ex) / 2, sy, state=self.Gdk.ModifierType.BUTTON1_MASK)
        self.mouse("move", ex, ey, state=self.Gdk.ModifierType.BUTTON1_MASK)
        self.mouse("release", ex, ey)
        self.assertEqual(v.sel["text"], "Page")
        self.draw()
        self.click_region(("tb", "color", "green"))
        hls = v.store.highlights(v.bid())
        self.assertEqual([(h["text"], h["color"], h["page"]) for h in hls], [("Page", "green", 0)])
        self.assertIsNone(v.sel)
        self.assertEqual(v.hl, (0, hls[0]["id"]))
        self.draw()
        self.click_region(("tb", "color", "pink"))                    # a chosen highlight changes colour
        self.assertEqual(v.store.highlights(v.bid())[0]["color"], "pink")
        v.store.set_highlight_note(v.bid(), hls[0]["id"], "remember")
        v.panel, v.panel_tab = "notes", "notes"
        self.draw()
        self.assertTrue(any(k[0] == "delete" for _r, k in v.hits))
        self.click_region(("delete", "hl", hls[0]["id"]))
        self.assertEqual(v.store.highlights(v.bid()), [])

    def test_clicking_a_saved_highlight_chooses_it_and_a_click_elsewhere_clears(self):
        v = self.open()
        self.settle()
        _text, chars = v.texts[0]
        hid = v.store.add_highlight(v.bid(), 0, [chars[0][:2] + chars[3][2:]], "Page", "yellow")
        self.draw()
        x, y = self.point_on(0, chars[1][0] + 1, (chars[1][1] + chars[1][3]) / 2)
        self.mouse("press", x, y)
        self.mouse("release", x, y)
        self.assertEqual(v.hl, (0, hid))
        ox, oy = self.point_on(0, 300, 560)
        self.mouse("press", ox, oy)
        self.mouse("release", ox, oy)
        self.assertIsNone(v.hl)

    def test_double_click_selects_a_word(self):
        v = self.open()
        self.settle()
        _text, chars = v.texts[0]
        x, y = self.point_on(0, chars[7][0] + 1, (chars[7][1] + chars[7][3]) / 2)       # inside "heading"
        e = self.Gdk.EventButton()
        e.type, e.x, e.y, e.button = self.Gdk.EventType._2BUTTON_PRESS, x, y, 1
        v.on_press(None, e)
        self.assertEqual(v.sel["text"], "heading")

    def test_bookmarks_toggle_and_show_in_the_panel(self):
        v = self.open()
        self.key("b")
        self.assertEqual(v.store.bookmarks(v.bid()), [0])
        self.key("m")
        self.draw()
        self.assertEqual(v.panel_tab, "marks")
        self.click_region(("unmark", 0))
        self.assertEqual(v.store.bookmarks(v.bid()), [])

    def test_page_notes_are_added_from_the_panel_data(self):
        v = self.open()
        nid = v.store.add_note(v.bid(), 1, "check chapter two")
        v.panel, v.panel_tab = "notes", "notes"
        self.draw()
        self.click_region(("delete", "note", nid))
        self.assertEqual(v.store.notes(v.bid()), [])

    def test_go_to_page_box(self):
        v = self.open()
        v.open_entry("goto", "")
        v.entry.set_text("7")
        v.entry.emit("activate")
        self.pump_until(lambda: not v.anim, 5)
        self.assertEqual(v.cur_page(), 6)
        self.assertIsNone(v.entry_mode)

    def test_themes_change_the_page_pictures(self):
        v = self.open()
        self.settle()
        light = v.page_surface(0, "light")
        self.key("d")                                                 # auto, light, sepia, night: the next one
        names = [v.theme_name]
        for _ in range(3):
            self.key("d")
            names.append(v.theme_name)
        self.assertEqual(sorted(names), ["auto", "light", "night", "sepia"])
        v.theme_name = "night"
        self.settle()
        night = v.page_surface(0, "night")
        self.assertIsNotNone(night)
        self.assertIsNot(night, light)
        v.store.flush()
        self.assertIn(self.BK.Store(self.BK.DATA_FILE).pref("theme"), self.BK.THEMES)
        v.theme_name = "light"

    def test_reading_time_and_pages_are_counted(self):
        v = self.open()
        t = time.time()
        v.tracker.poke(t)
        for i in range(0, 12, 2):
            v.tracker.tick(t + i, v.bid(), 0)
        secs, pages = v.store.today(t + 10)
        self.assertGreaterEqual(secs, 8)
        self.assertEqual(pages, 1)
        self.draw()
        v.close_book()
        self.draw()                                                   # the library shows today's reading

    def test_an_unreadable_pdf_shows_a_message_not_a_crash(self):
        bad = f"{self.books_dir}/broken.pdf"
        with open(bad, "wb") as f:
            f.write(b"%PDF-1.4 garbage")
        v = self.view
        v.refresh()
        self.assertTrue(self.pump_until(lambda: len(v.entries) == 3 and not v.indexing, 10))
        e = next(e for e in v.entries if e["path"] == bad)
        v.open_book(e)
        self.assertTrue(self.pump_until(lambda: v.failed != "", 8))
        self.draw()
        self.key("Escape")
        self.assertEqual(v.mode, "library")
        os.remove(bad)
        v.refresh()
        self.pump_until(lambda: len(v.entries) == 2, 10)


if __name__ == "__main__":
    unittest.main()
