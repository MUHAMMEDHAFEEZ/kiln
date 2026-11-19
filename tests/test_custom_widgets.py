"""The cards of widgets you build (note, command output, shortcuts, picture) and their edit layer. Skipped without a display."""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "share")
os.environ.setdefault("CANBERRA_DRIVER", "null")


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display")
class CustomCardTests(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.store = self.WG.Widgets(f"{self.tmp.name}/w.json")
        self.opened, self.ran = [], []
        self.saved = (self.app.open_url, self.app.run)
        self.app.open_url = self.opened.append
        self.app.run = self.ran.append

    def tearDown(self):
        self.app.open_url, self.app.run = self.saved

    def card(self, wtype, title="T", **fields):
        wid = self.store.add(wtype, title, **fields)
        return self.app.CustomCard(wid, self.store)

    def draw(self, card):
        import cairo
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, card.W, card.H)
        card._on_draw(None, cairo.Context(surf))
        return surf

    def press(self, card, key, button=1):
        x, y, w, h = next(r for r, k in card.hits if k == key)
        ev = self.Gdk.EventButton()
        ev.type, ev.x, ev.y, ev.button = self.Gdk.EventType.BUTTON_PRESS, x + w / 2, y + h / 2, button
        card._on_press(None, ev)

    def test_every_kind_draws_empty_and_filled(self):
        png = f"{self.tmp.name}/p.png"
        import cairo
        cairo.ImageSurface(cairo.FORMAT_ARGB32, 60, 40).write_to_png(png)
        for wtype, fields in (("note", {"text": "hello\nمرحبا"}), ("command", {"command": "echo hi"}),
                              ("links", {"items": [{"label": "a", "target": "https://example.com"}]}),
                              ("image", {"path": png})):
            card = self.card(wtype, "T", **fields)
            self.assertEqual(card.TITLE, "T")
            self.draw(card)                                                    # filled
            card.store.update(card.wid, **{k: "" if isinstance(v, str) else [] for k, v in fields.items()})
            card.apply()
            self.draw(card)                                                    # empty: a hint, no error
        broken = self.card("image", path="/no/such.png")
        self.draw(broken)
        self.assertIsNone(broken.pix)

    def test_a_command_widget_runs_its_command_and_shows_the_output(self):
        card = self.card("command", "Echo", command="echo from-the-widget", interval=5)
        card.refresh()
        end = time.time() + 5
        while card.busy and time.time() < end:
            while self.Gtk.events_pending():
                self.Gtk.main_iteration()
            time.sleep(0.02)
        self.assertEqual((card.out, card.ok), ("from-the-widget", True))
        self.draw(card)
        self.assertIn(("refresh", None), [k for _r, k in card.hits])

    def test_a_shortcut_opens_a_link_a_folder_or_runs_a_command(self):
        card = self.card("links", "Go", items=[{"label": "web", "target": "https://example.com/x"},
                                               {"label": "dir", "target": "/tmp"},
                                               {"label": "cmd", "target": "make test"}])
        self.draw(card)
        for i in range(3):
            self.press(card, ("link", i))
        self.assertEqual(self.opened, ["https://example.com/x"])
        self.assertEqual(self.ran, [["xdg-open", "/tmp"], ["/bin/bash", "-c", "make test"]])

    def test_right_click_asks_the_owner_for_a_menu(self):
        card = self.card("note", "N", text="x")
        got = []
        card.owner = type("O", (), {"menu": staticmethod(lambda c, ev: got.append(c.wid))})()
        ev = self.Gdk.EventButton()
        ev.type, ev.x, ev.y, ev.button = self.Gdk.EventType.BUTTON_PRESS, 20, 20, 3
        self.assertTrue(card._on_press(None, ev))
        self.assertEqual(got, [card.wid])

    def test_the_edit_layer_of_a_built_widget_has_an_edit_button(self):
        calls = []

        class Owner:
            def drag_start(self, layer): pass
            def toggle_select(self, key): pass
            def moved(self, *a): pass
            def resized(self, *a): pass
            def finished(self, *a): pass
            def hide_widget(self, key): calls.append(("hide", key))
            def edit_widget(self, key): calls.append(("edit", key))
            def layer_menu(self, layer, ev): calls.append(("menu", layer.key))
        win = self.Gtk.Window()
        win._pos = (0, 0)
        layer = self.app.EditLayer(win, "custom_w1", "T", True, Owner())
        win.add(layer)
        win.resize(380, 260)
        layer.show()
        win.show_all()
        while self.Gtk.events_pending():
            self.Gtk.main_iteration()
        w = layer.get_allocated_width()
        self.assertEqual(layer._zone(w - 75, 30), "edit")
        self.assertEqual(layer._zone(w - 30, 30), "hide")
        win2 = self.Gtk.Window()
        win2._pos = (0, 0)
        plain = self.app.EditLayer(win2, "term", "T", True, Owner())
        win2.add(plain)
        win2.resize(380, 260)
        plain.show()
        win2.show_all()
        while self.Gtk.events_pending():
            self.Gtk.main_iteration()
        self.assertEqual(plain._zone(plain.get_allocated_width() - 75, 30), "move")     # only widgets you built have Edit
        win2.destroy()
        for button in (1, 3):
            ev = self.Gdk.EventButton()
            ev.type, ev.x, ev.y, ev.button = self.Gdk.EventType.BUTTON_PRESS, w - 75, 30, button
            layer.on_press(None, ev)
        self.assertEqual(calls, [("edit", "custom_w1"), ("menu", "custom_w1")])
        win.destroy()


if __name__ == "__main__":
    unittest.main()
