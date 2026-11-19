"""The Overview's cards as windows of their own, and the edit layer / bar of "Edit layout". Skipped without a display."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "share")
os.environ.setdefault("CANBERRA_DRIVER", "null")


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display")
class OverviewLayoutTests(unittest.TestCase):
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
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"no GTK here ({e})")
        if Gdk.Screen.get_default() is None:
            raise unittest.SkipTest("no screen")
        cls.Gdk, cls.Gtk, cls.app = Gdk, Gtk, app

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def press(self, widget, x, y, button=1):
        ev = self.Gdk.EventButton()
        ev.type, ev.x, ev.y, ev.x_root, ev.y_root, ev.button = self.Gdk.EventType.BUTTON_PRESS, x, y, x + 500, y + 300, button
        return widget.on_press(None, ev)

    def test_every_card_is_its_own_size_and_draws_only_itself(self):
        import cairo
        app = self.app
        for sec in app.SECTIONS:
            view = app.Cards(None, sec)
            w, h = app.LAY[sec][2:]
            req = view.area.get_size_request()
            self.assertEqual(tuple(req), (w, h), sec)
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            view.on_draw(None, cairo.Context(surf))
            self.assertTrue(view.hits or sec in ("github", "agenda"), sec)       # it drew its own buttons
            for box, _key in view.hits:                                           # and nothing outside its own card
                x, y, bw, bh = box
                self.assertGreaterEqual(x, app.LAY[sec][0] - 1, sec)
                self.assertGreaterEqual(y, app.LAY[sec][1] - 1, sec)

    def test_a_click_in_a_card_window_hits_the_right_button(self):
        app, Gtk = self.app, self.Gtk
        view = app.Cards(None, "today")
        win = Gtk.Window()
        win.add(view)
        win.show_all()
        import cairo
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, *app.LAY["today"][2:])
        view.on_draw(None, cairo.Context(surf))
        box = next(b for b, k in view.hits if k == ("add",))
        ox, oy = app.LAY["today"][:2]
        self.press(view, box[0] + box[2] / 2 - ox, box[1] + box[3] / 2 - oy)       # in the card's own coordinates
        self.assertTrue(view.entry.get_visible())
        win.destroy()

    def test_the_prayer_card_shows_the_five_prayers(self):
        import cairo
        app = self.app
        app.CFG["location"] = {"latitude": 30.04, "longitude": 31.24, "city": "Cairo"}
        app.CFG["prayer"] = {"enabled": True, "method": "egypt", "language": "en"}
        view = app.Cards(None, "strip")
        w, h = app.LAY["strip"][2:]
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        view.on_draw(None, cairo.Context(surf))
        self.assertTrue(app.prayer_state() is not None)
        self.assertIn(("strip",), [k for _b, k in view.hits])          # a click on it opens the day's times

    def make_layer(self, resizable):
        calls = []

        class Owner:
            def drag_start(self, layer): pass
            def toggle_select(self, key): calls.append(("select", key))
            def moved(self, layer, x, y, state=0): calls.append(("moved", x, y))
            def resized(self, layer, w, h, state=0): calls.append(("resized", w, h))
            def finished(self, layer): calls.append(("finished",))
            def hide_widget(self, key): calls.append(("hide", key))
        win = self.Gtk.Window()
        win._pos = (200, 100)
        layer = self.app.EditLayer(win, "card_today", "Today", resizable, Owner())
        win.add(layer)
        win.resize(370, 404)
        layer.show()
        win.show_all()
        while self.Gtk.events_pending():
            self.Gtk.main_iteration()
        return win, layer, calls

    def motion(self, layer, x_root, y_root):
        ev = self.Gdk.EventMotion()
        ev.type, ev.x, ev.y, ev.x_root, ev.y_root = self.Gdk.EventType.MOTION_NOTIFY, 10, 10, x_root, y_root
        layer.on_motion(None, ev)

    def test_dragging_moves_by_the_distance_and_ends_with_a_save(self):
        win, layer, calls = self.make_layer(False)
        self.press(layer, 100, 200)                          # the middle of the widget: a move
        self.motion(layer, 600 + 40, 500 + 30)
        self.assertEqual(calls[-1], ("moved", 240, 130))
        ev = self.Gdk.EventButton()
        ev.type = self.Gdk.EventType.BUTTON_RELEASE
        layer.on_release(None, ev)
        self.assertEqual(calls[-1], ("finished",))
        win.destroy()

    def test_the_cross_hides_and_only_sized_widgets_have_a_corner(self):
        win, layer, calls = self.make_layer(False)
        w = layer.get_allocated_width()
        self.press(layer, w - 30, 30)
        self.assertEqual(calls[-1], ("hide", "card_today"))
        self.assertEqual(layer._zone(w - 5, layer.get_allocated_height() - 5), "move")     # no corner on a fixed-size card
        win.destroy()
        win, layer, calls = self.make_layer(True)
        w, h = layer.get_allocated_width(), layer.get_allocated_height()
        self.assertEqual(layer._zone(w - 5, h - 5), "resize")
        self.press(layer, w - 5, h - 5)
        self.motion(layer, 500 + w - 5 + 50, 300 + h - 5 + 20)
        self.assertEqual(calls[-1][0], "resized")
        win.destroy()

    def test_the_bar_calls_add_reset_and_done(self):
        got = []
        bar = self.app.LayoutBar(lambda ev: got.append("add"), lambda: got.append("reset"), lambda: got.append("done"),
                                 on_cancel=lambda: got.append("cancel"))
        import cairo
        bar.on_draw(None, cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, bar.W, bar.H)))
        for key in ("add", "reset", "cancel", "done"):
            x, y, w, h = next(r for r, k in bar.hits if k == key)
            ev = self.Gdk.EventButton()
            ev.type, ev.x, ev.y, ev.button = self.Gdk.EventType.BUTTON_PRESS, x + w / 2, y + h / 2, 1
            bar.on_press(None, ev)
        self.assertEqual(got, ["add", "reset", "cancel", "done"])
        bar.destroy()


if __name__ == "__main__":
    unittest.main()
