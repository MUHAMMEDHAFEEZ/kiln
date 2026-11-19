"""The task details window: a click on a task shows it first (nothing opens in the browser), with the actions
that fit the task. Skipped without a display."""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "share")
os.environ.setdefault("CANBERRA_DRIVER", "null")

TASK = dict(service="larkbase", id="rec1", title="Fix the checkout total when a coupon and a gift card are both used",
            status="Not yet started", done=False, owners=["Mona Ali", "Omar"], priority="Important",
            project="SHOP", detail="Backend", due=time.time() + 3 * 86400, url="https://x.larksuite.com/base/b?table=t&record=rec1",
            note="Steps:\n1. add a coupon\n2. pay with a gift card\nThe total is wrong.", attachments=["bug.png", "log.txt"],
            groups=["SHOP"], start=time.time() - 86400,
            desc="1. There are two levels of reminders\nA. Personal preferences\n\u0639\u0645\u0644 \u0647\u0630\u0627 commands \u0627\u0644\u0644\u064a \u0628\u0639\u062f",
            links=[("Swagger", "https://example.test/docs"), ("Broken", "")])


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display")
class TaskPopupTests(unittest.TestCase):
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
        cls.app, cls.Gtk = app, Gtk

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.pop = self.app.TaskPopup()
        self.opened, self.copied, self.moved = [], [], []
        self.saved = (self.app.open_url, self.app.copy_text, self.app.move_task, self.app.start_task)
        self.app.open_url = self.opened.append
        self.app.copy_text = self.copied.append
        self.app.move_task = lambda t, k: self.moved.append((t["id"], k))
        self.app.start_task = lambda t, ev=None, **kw: self.moved.append((t["id"], "start"))

    def tearDown(self):
        self.app.open_url, self.app.copy_text, self.app.move_task, self.app.start_task = self.saved
        self.pop.destroy()

    def press(self, key):
        x, y, w, h = next(r for r, k in self.pop.hits if k == key)

        class Ev:
            button, x_root, y_root, time = 1, 100, 100, 0
        ev = Ev()
        ev.x, ev.y = x + w / 2, y + h / 2
        self.pop.on_press(None, ev)

    def test_click_shows_details_not_the_web(self):
        self.pop.show(TASK)
        self.assertEqual(self.opened, [])
        keys = [k for _r, k in self.pop.hits]
        for want in (("start",), ("move", "done"), ("move", "doing"), ("open",), ("copy",), ("close",)):
            self.assertIn(want, keys)
        self.assertGreater(self.pop.H, 300)

    def test_open_button_opens_the_link(self):
        self.pop.show(TASK)
        self.press(("open",))
        self.assertEqual(self.opened, [TASK["url"]])

    def test_actions_follow_the_status(self):
        self.pop.show(dict(TASK, status="Completed", done=True))
        keys = [k for _r, k in self.pop.hits]
        self.assertNotIn(("start",), keys)
        self.assertIn(("move", "doing"), keys)          # Reopen
        self.pop.show(dict(TASK, status="Ongoing"))
        keys = [k for _r, k in self.pop.hits]
        self.assertIn(("move", "todo"), keys)
        self.assertNotIn(("move", "doing"), keys)

    def test_moves_and_start(self):
        self.pop.show(TASK)
        self.press(("move", "done"))
        self.pop.show(dict(TASK, status="Not yet started"))
        self.press(("start",))
        self.assertEqual(self.moved, [("rec1", "done"), ("rec1", "start")])

    def test_plain_task_of_another_service(self):
        t = self.app._plain_task(dict(service="todoist", guid="9", text="Buy milk", due=None, url="https://todoist.com/9"))
        self.pop.show(t)
        keys = [k for _r, k in self.pop.hits]
        self.assertIn(("open",), keys)
        self.assertNotIn(("start",), keys)

    def test_task_without_link_copies_title(self):
        self.pop.show(dict(TASK, url=""))
        self.press(("copytitle",))
        self.assertEqual(self.copied, [TASK["title"]])

    def test_each_task_is_a_tab(self):
        self.pop.show(TASK)
        self.pop.show(dict(TASK, id="rec2", title="Second task"))
        self.assertEqual([e["t"]["id"] for e in self.pop.tabs], ["rec1", "rec2"])
        self.assertEqual(self.pop.cur, 1)
        self.pop.show(TASK)                                  # the same task again: its tab, not a new one
        self.assertEqual((len(self.pop.tabs), self.pop.cur), (2, 0))
        self.press(("tab", 1))
        self.assertEqual(self.pop.t["id"], "rec2")
        self.press(("tabx", 1))
        self.assertEqual([e["t"]["id"] for e in self.pop.tabs], ["rec1"])
        self.press(("tabx", 0))
        self.assertFalse(self.pop.get_visible())

    def test_tabs_are_capped(self):
        for i in range(12):
            self.pop.show(dict(TASK, id=f"r{i}"))
        self.assertEqual(len(self.pop.tabs), self.pop.MAX_TABS)

    def test_description_links_and_copy(self):
        self.pop.show(TASK)
        keys = [k for _r, k in self.pop.hits]
        self.assertIn(("link", 0), keys)
        self.assertNotIn(("link", 1), keys)                 # an address that is not a real link is only shown
        self.press(("link", 0))
        self.assertEqual(self.opened, ["https://example.test/docs"])
        self.press(("copydesc",))
        self.assertTrue(self.copied[0].startswith("1. There are two levels"))

    def test_a_long_description_scrolls_and_keeps_the_buttons(self):
        self.pop.show(dict(TASK, desc="\n".join(f"line {i}" for i in range(120))))
        self.assertEqual(self.pop.body_h, self.pop.MAXBODY)
        self.assertGreater(self.pop.content_h, self.pop.body_h)
        before = self.pop.H

        class Ev:
            direction = self.app.Gdk.ScrollDirection.DOWN
        self.pop.on_scroll(None, Ev())
        self.assertGreater(self.pop.off, 0)
        self.assertEqual(self.pop.H, before)
        self.assertIn(("open",), [k for _r, k in self.pop.hits])

    def test_status_change_shows_at_once_and_keeps_the_window(self):
        self.pop.show(TASK)
        self.press(("move", "done"))
        self.assertTrue(self.pop.get_visible())
        self.assertEqual(self.pop.t["status"], "Completed")

    def test_renders(self):
        import cairo
        self.pop.show(TASK)
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.pop.W, self.pop.H)
        cr = cairo.Context(surf)
        self.pop.on_draw(None, cr)
        out = os.environ.get("KILN_SHOT")
        if out:
            surf.write_to_png(out)


if __name__ == "__main__":
    unittest.main()
