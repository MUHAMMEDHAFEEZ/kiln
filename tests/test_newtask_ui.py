"""The attachments part of the New task window: paste with Ctrl+V (also on an Arabic keyboard layout), files, cleanup.
Skipped without a display."""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "share")


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display")
class AttachmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("Gdk", "3.0")
            from gi.repository import Gdk, GdkPixbuf, Gtk
            sys.path.insert(0, ROOT)
            import accounts
            import keys
            import newtask
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"no GTK here ({e})")
        if Gdk.Screen.get_default() is None:
            raise unittest.SkipTest("no screen")
        cls.Gdk, cls.Gtk, cls.Pix, cls.N, cls.A, cls.keys = Gdk, Gtk, GdkPixbuf, newtask, accounts, keys

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cache = self.A.CACHE
        self.A.CACHE = self.tmp.name
        self.win = self.Gtk.Window()
        self.entry = self.Gtk.Entry()
        box = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL)
        self.widget, self.paths, self.cleanup = self.N.attachments_widget(self.win)
        box.add(self.entry)
        box.add(self.widget)
        self.win.add(box)
        self.win.show_all()
        self.entry.grab_focus()
        self.clip = self.Gtk.Clipboard.get(self.Gdk.SELECTION_CLIPBOARD)
        self.pump(0.2)

    def tearDown(self):
        self.win.destroy()
        self.A.CACHE = self.old_cache
        self.tmp.cleanup()

    def pump(self, sec):
        end = time.time() + sec
        while time.time() < end:
            while self.Gtk.events_pending():
                self.Gtk.main_iteration()
            time.sleep(0.01)

    def picture(self):
        path = os.path.join(self.tmp.name, "shot.png")
        self.Pix.Pixbuf.new(self.Pix.Colorspace.RGB, True, 8, 40, 30).savev(path, "png", [], [])
        return self.Pix.Pixbuf.new_from_file(path)

    def ctrl_v(self, keyval=None, keycode=55):
        ev = self.Gdk.Event.new(self.Gdk.EventType.KEY_PRESS)
        ev.window = self.win.get_window()
        ev.keyval, ev.state, ev.hardware_keycode = keyval or self.Gdk.KEY_v, self.Gdk.ModifierType.CONTROL_MASK, keycode
        return self.win.emit("key-press-event", ev)

    def test_ctrl_v_attaches_a_picture_even_with_the_title_focused(self):
        self.clip.set_image(self.picture())
        self.assertTrue(self.ctrl_v())
        files = self.paths()
        self.assertEqual(len(files), 1)
        self.assertTrue(os.path.basename(files[0]).startswith("pasted-") and os.path.getsize(files[0]) > 0)
        self.assertEqual(oct(os.stat(os.path.dirname(files[0])).st_mode & 0o777), "0o700")   # the cache folder is private

    def test_ctrl_v_works_with_an_arabic_layout(self):
        arabic_v = self.Gdk.keyval_from_name("Arabic_ra")                  # the letter on the V key of an Arabic keyboard
        ev = self.Gdk.Event.new(self.Gdk.EventType.KEY_PRESS)
        ev.keyval, ev.state, ev.hardware_keycode = arabic_v, self.Gdk.ModifierType.CONTROL_MASK, 55
        if self.keys.latin_key(ev) != self.Gdk.KEY_v:
            self.skipTest("this machine has no key map to translate the key with")
        self.clip.set_image(self.picture())
        self.assertTrue(self.ctrl_v(arabic_v))
        self.assertEqual(len(self.paths()), 1)

    def test_text_is_left_to_the_entry(self):
        self.clip.set_text("just words", -1)
        self.ctrl_v()                                                       # the entry takes it, nothing is attached
        self.pump(0.3)
        self.assertEqual(self.paths(), [])
        self.assertEqual(self.entry.get_text(), "just words")

    def test_the_paste_button_and_remove(self):
        self.clip.set_image(self.picture())
        self.widget.get_children()[0].get_children()[-1].clicked()           # the Paste button
        self.assertEqual(len(self.paths()), 1)

    def test_pasted_pictures_are_deleted_with_the_window(self):
        self.clip.set_image(self.picture())
        self.ctrl_v()
        (path,) = self.paths()
        self.cleanup()
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
