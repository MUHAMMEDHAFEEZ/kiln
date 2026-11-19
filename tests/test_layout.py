"""The views of the desktop and the saved places of their widgets (no GTK needed)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
import layout as LY  # noqa: E402

KEYS = ["today", "github", "term", "dock_git", "dock_system"]


class WidgetPlaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = f"{self.tmp.name}/layout.json"

    def make(self):
        return LY.Layout(self.path, KEYS, prefixes=("custom_",))

    def test_nothing_saved_means_the_defaults(self):
        lay = self.make()
        self.assertEqual(lay.position("today", (10, 20)), (10, 20))
        self.assertEqual(lay.size("term", (900, 500)), (900, 500))
        self.assertTrue(lay.visible("today", True))
        self.assertFalse(lay.visible("dock_git", False))

    def test_move_resize_hide_add_survive_a_restart(self):
        lay = self.make()
        lay.move("today", 300, 120)
        lay.resize("term", 1000, 600)
        lay.hide("github")
        lay.show("dock_git", default_hidden=True, pos=(500, 400))
        lay.save()
        again = self.make()
        self.assertEqual(again.position("today", (0, 0)), (300, 120))
        self.assertEqual(again.size("term", (1, 1)), (1000, 600))
        self.assertFalse(again.visible("github", True))
        self.assertTrue(again.visible("dock_git", False))
        self.assertEqual(again.position("dock_git", (0, 0)), (500, 400))

    def test_showing_a_hidden_default_widget_clears_the_mark(self):
        lay = self.make()
        lay.hide("today")
        lay.show("today")
        self.assertTrue(lay.visible("today", True))
        self.assertNotIn("today", lay.d)

    def test_reset_forgets_the_changes_of_this_view_only(self):
        lay = self.make()
        lay.move("today", 1, 2)
        lay.use_mode("coding")
        lay.move("today", 5, 6)
        lay.reset()
        lay.save()
        again = self.make()
        again.use_mode("coding")
        self.assertEqual(again.d, {})
        again.use_mode("overview")
        self.assertEqual(again.position("today", (0, 0)), (1, 2))

    def test_a_hand_edited_file_is_checked(self):
        with open(self.path, "w") as f:
            json.dump({"version": 3, "custom": {"v1": "Ok", "x9": "bad id", "v2": "", "v3": "Overview"}, "hidden": ["nope", "focus"],
                       "places": {"overview": {
                           "today": {"x": "oops", "y": 5, "w": 5, "h": 9999999, "hidden": "yes"},
                           "nope": {"x": 1, "y": 1},                       # not a widget that exists
                           "term": {"x": 10, "y": 20, "w": 800, "h": 400, "hidden": True}}, "v1": {"today": {"x": 7}}}}, f)
        lay = self.make()
        self.assertEqual(lay.get("today"), {"y": 5})
        self.assertNotIn("nope", lay.d)
        self.assertEqual(lay.get("term"), {"x": 10, "y": 20, "w": 800, "h": 400, "hidden": True})
        self.assertEqual(lay.custom, {"v1": "Ok"})                         # a bad id, an empty name, a name taken by a built-in view: dropped
        self.assertEqual(lay.hidden_views, {"focus"})
        with open(self.path, "w") as f:
            f.write("{nope")
        self.assertEqual(self.make().d, {})

    def test_snap_and_clamp(self):
        self.assertEqual(LY.snap(13), 16)
        self.assertEqual(LY.snap(11), 8)
        self.assertEqual(LY.Layout.clamp(5000, 5000, 400, 300, (1920, 1080)), (1872, 1032))
        self.assertEqual(LY.Layout.clamp(-9000, -50, 400, 300, (1920, 1080)), (-352, 0))
        self.assertEqual(LY.Layout.clamp(100, 100, 400, 300, (1920, 1080)), (100, 100))

    def test_widgets_you_build_come_and_go(self):
        with open(self.path, "w") as f:
            json.dump({"version": 3, "custom": {"v1": "Design"}, "hidden": [], "places": {
                "overview": {"custom_w1": {"x": 5, "y": 6}, "custom_w2": {"hidden": True}, "other_w3": {"x": 1}},
                "coding": {"custom_w1": {"added": True}}, "v1": {"custom_w1": {"x": 9, "y": 9}}}}, f)
        lay = self.make()
        self.assertEqual(sorted(lay.d), ["custom_w1", "custom_w2"])      # custom_ kept, a key nobody knows dropped
        lay.forget("custom_w1")
        lay.save()
        again = self.make()
        self.assertEqual(sorted(again.d), ["custom_w2"])
        again.use_mode("coding")
        self.assertEqual(again.d, {})
        again.use_mode("v1")
        self.assertEqual(again.d, {})

    def test_minimum_size(self):
        lay = self.make()
        lay.resize("term", 10, 10)
        self.assertEqual(lay.size("term", (0, 0)), (LY.MIN_W, LY.MIN_H))


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = f"{self.tmp.name}/layout.json"

    def make(self):
        return LY.Layout(self.path, KEYS)

    def test_the_list_of_views(self):
        lay = self.make()
        self.assertEqual(lay.visible_views(), ["overview", "coding", "focus", "tasks", "books"])
        self.assertEqual(lay.visible_views(tasks=False), ["overview", "coding", "focus", "books"])
        self.assertEqual([lay.title(v) for v in lay.order()], ["Overview", "Coding", "Focus", "Tasks", "Books"])
        self.assertTrue(lay.free("overview") and lay.free("coding"))
        self.assertFalse(lay.free("focus") or lay.free("books") or lay.free("v1"))
        lay.use_mode("books")                                            # a view without a free layout shows the Overview's places
        self.assertEqual(lay.mode, "overview")

    def test_a_new_view_is_a_copy_of_another_and_appears_after_coding(self):
        lay = self.make()
        lay.move("today", 100, 100)
        vid = lay.create_view("  Design   desk ")
        self.assertEqual((vid, lay.custom[vid]), ("v1", "Design desk"))
        self.assertEqual(lay.visible_views(), ["overview", "coding", "v1", "focus", "tasks", "books"])
        self.assertTrue(lay.free(vid))
        lay.use_mode(vid)
        self.assertEqual(lay.position("today", (0, 0)), (100, 100))      # it started as a copy of the Overview
        lay.move("today", 500, 500)
        lay.use_mode("overview")
        self.assertEqual(lay.position("today", (0, 0)), (100, 100))      # and the Overview did not change
        lay.save()
        again = self.make()
        again.use_mode("v1")
        self.assertEqual(again.position("today", (0, 0)), (500, 500))
        self.assertEqual(again.title("v1"), "Design desk")
        self.assertEqual(again.find("design DESK"), "v1")
        self.assertEqual(again.find("coding"), "coding")
        self.assertIsNone(again.find("nothing"))

    def test_names_are_checked(self):
        lay = self.make()
        lay.create_view("Work")
        for bad in ("", "   ", "work", "WORK", "Overview", "coding", "bad\x07name"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                lay.create_view(bad)
        self.assertEqual(len(LY.clean_name("x" * 200)), LY.MAX_NAME)
        self.assertEqual(lay.rename_view("v1", "Desk"), "Desk")
        self.assertEqual(lay.rename_view("v1", "desk"), "desk")           # the same name in another case: allowed for itself
        with self.assertRaises(ValueError):
            lay.rename_view("overview", "Home")                          # the built-in names stay
        lay.create_view("Other")
        with self.assertRaises(ValueError):
            lay.rename_view("v2", "DESK")

    def test_the_order_of_the_list_is_yours(self):
        lay = self.make()
        lay.create_view("Work")
        self.assertEqual(lay.order(), ["overview", "coding", "v1", "focus", "tasks", "books"])
        lay.move_view("books", "overview")                                # Books first
        lay.move_view("coding")                                           # Coding last
        self.assertEqual(lay.order(), ["books", "overview", "v1", "focus", "tasks", "coding"])
        self.assertEqual(lay.visible_views(tasks=False), ["books", "overview", "v1", "focus", "coding"])
        lay.move_view("overview", "overview")                             # onto itself, or an unknown view: nothing
        lay.move_view("overview", "nothing")
        lay.move_view("nothing", "books")
        self.assertEqual(lay.order()[0:2], ["books", "overview"])
        lay.hide_view("v1")
        lay.move_view("focus", "books")                                   # a hidden view keeps its place while others move
        self.assertEqual(lay.order(), ["focus", "books", "overview", "v1", "tasks", "coding"])
        self.assertEqual(lay.neighbours("overview"), ("books", "tasks"))  # the hidden v1 is skipped
        self.assertEqual(lay.neighbours("focus"), (None, "books"))
        self.assertEqual(lay.neighbours("coding"), ("tasks", None))
        self.assertEqual(lay.neighbours("v1"), (None, None))
        lay.save()
        again = self.make()
        self.assertEqual(again.order(), ["focus", "books", "overview", "v1", "tasks", "coding"])
        vid = again.create_view("Late", copy_from="books")                 # a new view goes after your last view (or Coding)
        self.assertEqual(again.order(), ["focus", "books", "overview", "v1", vid, "tasks", "coding"])
        again.delete_view(vid)
        self.assertNotIn(vid, again.order())

    def test_a_saved_order_with_gaps_is_repaired(self):
        with open(self.path, "w") as f:
            json.dump({"version": 3, "custom": {"v1": "A", "v2": "B"}, "places": {},
                       "order": ["books", "ghost", "overview", "books", "v2"]}, f)
        lay = self.make()
        # unknown and repeated entries are dropped; what is missing goes after the one before it in the default order
        self.assertEqual(lay.order(), ["books", "overview", "coding", "v1", "v2", "focus", "tasks"])

    def test_a_snapshot_brings_everything_back(self):
        lay = self.make()
        lay.move("today", 1, 2)
        lay.create_view("Work")
        snap = lay.snapshot()
        lay.move("today", 9, 9)
        lay.hide_view("focus")
        lay.rename_view("v1", "Other")
        lay.move_view("books", "overview")
        lay.create_view("Third")
        lay.restore(snap)
        self.assertEqual((lay.custom, lay.hidden_list(), lay.order()),
                         ({"v1": "Work"}, [], ["overview", "coding", "v1", "focus", "tasks", "books"]))
        lay.use_mode("overview")
        self.assertEqual(lay.position("today", (0, 0)), (1, 2))
        lay.move("today", 5, 5)                                           # the snapshot is a copy: later changes do not leak into it
        lay.restore(snap)
        self.assertEqual(lay.position("today", (0, 0)), (1, 2))

    def test_the_most_views(self):
        lay = self.make()
        for i in range(LY.MAX_VIEWS):
            lay.create_view(f"View {i}")
        with self.assertRaises(ValueError):
            lay.create_view("One more")

    def test_any_view_can_be_hidden_and_shown_again_but_not_deleted_unless_it_is_yours(self):
        lay = self.make()
        lay.create_view("Work")
        lay.hide_view("focus")
        lay.hide_view("v1")
        self.assertEqual(lay.visible_views(), ["overview", "coding", "tasks", "books"])
        self.assertEqual(lay.hidden_list(), ["v1", "focus"])
        lay.save()
        again = self.make()
        self.assertEqual(again.hidden_list(), ["v1", "focus"])
        again.show_view("focus")
        self.assertEqual(again.hidden_list(), ["v1"])
        with self.assertRaises(ValueError):
            again.delete_view("coding")                                  # the built-in ones are hidden, never deleted
        again.delete_view("v1")                                          # a view of yours: deleted, hidden or not
        self.assertEqual(again.custom, {})
        self.assertEqual(again.hidden_list(), [])
        self.assertNotIn("v1", again.places)

    def test_one_view_always_stays_in_the_list(self):
        lay = self.make()
        for v in ("coding", "focus", "tasks", "books"):
            lay.hide_view(v)
        with self.assertRaises(ValueError):
            lay.hide_view("overview")
        with self.assertRaises(ValueError):
            lay.hide_view("nothing")
        lay.hide_view("tasks")                                           # hiding what is already hidden is fine
        self.assertEqual(lay.visible_views(), ["overview"])
        self.assertEqual(lay.visible_views(tasks=False), ["overview"])   # a board that is not connected has no Tasks button

    def test_a_file_that_hides_everything_shows_everything(self):
        with open(self.path, "w") as f:
            json.dump({"version": 3, "custom": {}, "hidden": list(LY.BUILTIN), "places": {}}, f)
        self.assertEqual(self.make().hidden_list(), [])

    def test_a_version_1_file_becomes_the_overview(self):
        with open(self.path, "w") as f:
            json.dump({"version": 1, "overview": {"today": {"x": 5, "y": 6}, "nope": {"x": 1}}}, f)
        lay = self.make()
        self.assertEqual(lay.position("today", (0, 0)), (5, 6))
        self.assertEqual(lay.custom, {})
        lay.save()
        with open(self.path) as f:
            self.assertEqual(json.load(f)["version"], 3)

    def test_named_layouts_of_the_last_version_become_views(self):
        with open(self.path, "w") as f:
            json.dump({"version": 2, "active": "Home", "hidden": ["Old"], "layouts": {
                "Home": {"overview": {"today": {"x": 1, "y": 2}}, "coding": {"today": {"x": 3, "y": 4}}},
                "Work": {"overview": {"today": {"x": 5, "y": 6}}, "coding": {}},
                "Old": {"overview": {"today": {"x": 7, "y": 8}}},
                "Overview": {"overview": {}}}}, f)
        lay = self.make()
        self.assertEqual(lay.position("today", (0, 0)), (1, 2))          # the active layout is the Overview...
        lay.use_mode("coding")
        self.assertEqual(lay.position("today", (0, 0)), (3, 4))          # ...and the Coding mode
        self.assertEqual(sorted(lay.custom.values()), ["Old", "Overview 2", "Work"])    # the others became views (a taken name got a number)
        work = lay.find("Work")
        lay.use_mode(work)
        self.assertEqual(lay.position("today", (0, 0)), (5, 6))
        self.assertEqual(lay.hidden_list(), [lay.find("Old")])           # a hidden layout is a hidden view


class CommandLineTests(unittest.TestCase):
    def test_list_and_hidden(self):
        with tempfile.TemporaryDirectory() as t:
            old = (LY.FILE, LY.CONF)
            LY.FILE = f"{t}/layout.json"
            try:
                lay = LY.Layout(LY.FILE)
                lay.create_view("Work")
                lay.hide_view("focus")
                lay.save()
                import io
                from contextlib import redirect_stdout
                out = io.StringIO()
                with redirect_stdout(out):
                    LY.main(["layout.py", "list"])
                    LY.main(["layout.py", "hidden"])
                self.assertEqual(out.getvalue().split(), ["Overview", "Coding", "Work", "Tasks", "Books", "Focus"])
            finally:
                LY.FILE, LY.CONF = old


if __name__ == "__main__":
    unittest.main()
