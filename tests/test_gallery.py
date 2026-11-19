"""The widget gallery, the Scripts and Web page cards and Cancel in Edit layout. Skipped without a display."""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "share")
os.environ.setdefault("CANBERRA_DRIVER", "null")


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display")
class GalleryTests(unittest.TestCase):
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
            import widgets as WG
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"no GTK here ({e})")
        if Gdk.Screen.get_default() is None:
            raise unittest.SkipTest("no screen")
        cls.Gdk, cls.Gtk, cls.app, cls.WG = Gdk, Gtk, app, WG
        cls.saved_dirs = (app.APP, app.CACHE)
        app.APP, app.CACHE = f"{t}/conf", f"{t}/cache"     # a Web page widget keeps its cookies there: not in the real ones

    @classmethod
    def tearDownClass(cls):
        cls.app.APP, cls.app.CACHE = cls.saved_dirs
        cls.tmp.cleanup()

    def pump(self, sec=0.2):
        end = time.time() + sec
        while time.time() < end:
            while self.Gtk.events_pending():
                self.Gtk.main_iteration()
            time.sleep(0.01)

    def click(self, widget, x, y, button=1):
        ev = self.Gdk.EventButton()
        ev.type, ev.x, ev.y, ev.button = self.Gdk.EventType.BUTTON_PRESS, x, y, button
        widget.on_press(None, ev)

    # ---- the gallery
    def make_ctl(self):
        calls, state = [], {"on": {"card_today"}}

        class Ctl:
            @staticmethod
            def title(): return "Design"

            @staticmethod
            def entries():
                return [dict(key="card_today", title="Today", desc="Your tasks", icon="check-circle", group="native",
                             on="card_today" in state["on"], ok=True),
                        dict(key="dock_board", title="Tasks", desc="Your board", icon="check-circle", group="native",
                             on=False, ok=False, reason="Connect a board first."),
                        dict(key="custom_w1", title="Ideas", desc="A note", icon="check", group="mine",
                             on="custom_w1" in state["on"], ok=True)]

            @staticmethod
            def kinds(): return [dict(key=t, title=t.title(), desc="d", icon="folder") for t in ("note", "web")]

            @staticmethod
            def toggle(key): (state["on"].discard if key in state["on"] else state["on"].add)(key); calls.append(("toggle", key))

            @staticmethod
            def create(kind): calls.append(("create", kind))

            @staticmethod
            def edit(key): calls.append(("edit", key))

            @staticmethod
            def duplicate(key): calls.append(("duplicate", key))

            @staticmethod
            def delete(key): calls.append(("delete", key))

            @staticmethod
            def closed(): calls.append(("closed",))
        return Ctl, calls, state

    def tiles(self, gal):
        return [fb_child.get_child() for _l, fb, _n in gal.sections for fb_child in fb.get_children()]

    def test_the_gallery_lists_kinds_kiln_widgets_and_yours(self):
        ctl, calls, state = self.make_ctl()
        gal = self.app.GAL.Gallery(ctl)
        gal.show_all()
        self.pump()
        names = [t.entry["title"] for t in self.tiles(gal)]
        self.assertEqual(names, ["Note", "Web", "Today", "Tasks", "Ideas"])
        self.assertIn("Design", gal.sub_label.get_text())
        gal.destroy()

    def test_a_click_adds_or_takes_off_and_an_unavailable_one_does_nothing(self):
        ctl, calls, state = self.make_ctl()
        gal = self.app.GAL.Gallery(ctl)
        today, board, mine = [t for t in self.tiles(gal) if t.entry["title"] in ("Today", "Tasks", "Ideas")]
        self.click(today, 20, 20)
        self.assertEqual(calls, [("toggle", "card_today")])
        self.assertNotIn("card_today", state["on"])
        today2 = next(t for t in self.tiles(gal) if t.entry["title"] == "Today")           # the gallery was rebuilt: it shows it off
        self.assertFalse(today2.entry["on"])
        self.click(board, 20, 20)                                                         # not available (no board)
        self.assertEqual(len(calls), 1)
        create = next(t for t in self.tiles(gal) if t.entry["title"] == "Web")
        self.click(create, 20, 20)
        self.assertEqual(calls[-1], ("create", "web"))
        gal.destroy()

    def test_search_filters_the_tiles_and_their_sections(self):
        ctl, calls, state = self.make_ctl()
        gal = self.app.GAL.Gallery(ctl)
        gal.show_all()
        gal.search.set_text("tasks")
        self.pump(0.3)
        shown = [t.entry["title"] for t in self.tiles(gal) if t.get_parent().get_visible()]
        self.assertEqual(sorted(shown), ["Tasks", "Today"])                               # "Your tasks" in Today's text matches too
        gal.search.set_text("zzz")
        self.pump(0.3)
        self.assertEqual([t for t in self.tiles(gal) if t.get_parent().get_visible()], [])
        gal.search.set_text("")
        self.pump(0.3)
        self.assertEqual(len([t for t in self.tiles(gal) if t.get_parent().get_visible()]), 5)
        gal.destroy()

    def test_my_widgets_have_a_menu_and_native_ones_do_not(self):
        ctl, calls, state = self.make_ctl()
        gal = self.app.GAL.Gallery(ctl)
        mine = next(t for t in self.tiles(gal) if t.entry["title"] == "Ideas")
        native = next(t for t in self.tiles(gal) if t.entry["title"] == "Today")
        self.assertIsNotNone(mine.on_menu)
        self.assertIsNone(native.on_menu)
        self.assertEqual(mine._zone(self.app.GAL.TILE_W - 10, self.app.GAL.TILE_H - 10), "menu")
        self.assertEqual(mine._zone(20, 20), "tile")
        got = []
        saved = self.app.popup_menu
        self.app.popup_menu = lambda ev, items: got.append([label for label, _cb in items])
        try:
            self.click(mine, 20, 20, button=3)
        finally:
            self.app.popup_menu = saved
        self.assertEqual(got[0][:3], ["Edit…", "Duplicate", "Add to this view"])
        self.assertIn("Delete…", got[0])
        gal.destroy()

    def test_done_and_escape_close_it(self):
        ctl, calls, state = self.make_ctl()
        gal = self.app.GAL.Gallery(ctl)
        gal.show_all()
        gal.close_gallery()
        self.assertFalse(gal.get_visible())
        self.assertEqual(calls[-1], ("closed",))
        gal.destroy()

    # ---- a Scripts widget
    def test_a_script_runs_in_the_background_and_shows_its_result(self):
        store = self.WG.Widgets(f"{self.tmp.name}/w.json")
        wid = store.add("scripts", "Ops", items=[{"label": "ok", "command": "echo fine"},
                                                  {"label": "bad", "command": "echo nope >&2; exit 3"}])
        card = self.app.CustomCard(wid, store)
        import cairo
        card._on_draw(None, cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, card.W, card.H)))
        self.assertEqual([k for _r, k in card.hits if k[0] == "run"], [("run", 0), ("run", 1)])
        card.run_script(0)
        card.run_script(1)
        end = time.time() + 5
        while any((card.runs.get(i) or {}).get("state") == "running" for i in (0, 1)) and time.time() < end:
            self.pump(0.05)
        self.assertEqual((card.runs[0]["state"], card.runs[0]["out"]), ("ok", "fine"))
        self.assertEqual((card.runs[1]["state"], card.runs[1]["out"]), ("fail", "nope"))
        card._on_draw(None, cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, card.W, card.H)))

    # ---- a Web page widget
    def test_a_web_widget_only_loads_web_addresses(self):
        wk = self.app.webkit()
        if wk is None:
            self.skipTest("no WebKit")
        store = self.WG.Widgets(f"{self.tmp.name}/w2.json")
        wid = store.add("web", "Page", url="")
        card = self.app.WebCard(wid, store)
        opened, saved = [], self.app.open_url
        self.app.open_url = opened.append

        class Req:
            def __init__(self, uri): self.uri = uri
            def get_uri(self): return self.uri

        class Act:
            def __init__(self, uri): self.uri = uri
            def get_request(self): return Req(self.uri)

        class Decision:
            def __init__(self, uri): self.act, self.ignored = Act(uri), False
            def get_navigation_action(self): return self.act
            def ignore(self): self.ignored = True
        try:
            nav, newwin = wk.PolicyDecisionType.NAVIGATION_ACTION, wk.PolicyDecisionType.NEW_WINDOW_ACTION
            for uri, handled in (("https://example.com/", False), ("http://localhost:3000/", False), ("about:blank", False),
                                 ("javascript:alert(1)", True), ("file:///etc/passwd", True), ("ftp://x.y/", True)):
                d = Decision(uri)
                self.assertEqual(card._policy(card.view, d, nav), handled, uri)
                self.assertEqual(d.ignored, handled, uri)
            d = Decision("https://example.com/popup")
            self.assertTrue(card._policy(card.view, d, newwin))                      # a new window: your browser, not a pop-up
            self.assertEqual(opened, ["https://example.com/popup"])
            d = Decision("javascript:evil()")
            self.assertTrue(card._policy(card.view, d, newwin))
            self.assertEqual(opened, ["https://example.com/popup"])                  # a script address never reaches the browser
        finally:
            self.app.open_url = saved
            card.view.destroy()


if __name__ == "__main__":
    unittest.main()
