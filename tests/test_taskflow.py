"""What the Lark Base board remembers and does for you: changes between refreshes, notifications, the standup note,
branch names and project folders for "Start working". No network, no Lark.

    python3 -m unittest discover -s tests
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
import accounts as A  # noqa: E402
import standup as S  # noqa: E402

NOW = datetime.datetime(2026, 11, 4, 10, 0).timestamp()          # a Wednesday morning
DAY = 86400


def task(i, title, status="Not yet started", owners=("Ada",), **kw):
    t = dict(id=f"rec{i}", title=title, status=status, done=status == "Completed", owners=list(owners), due=None,
             priority="", project="SHOP", detail="Backend", note="", groups=["SHOP"], today=False, service="larkbase",
             url=f"https://example.test/{i}")
    t.update(kw)
    return t


class ChangeTests(unittest.TestCase):
    def test_nothing_is_reported_on_the_first_sync(self):
        self.assertEqual(A.status_changes([], [task(1, "a")], NOW), [])
        self.assertEqual(A.status_changes(None, [task(1, "a")], NOW), [])

    def test_new_tasks_status_changes_and_new_owners(self):
        old = [task(1, "a", owners=["Ada"]), task(2, "b", "Ongoing"), task(3, "c")]
        new = [task(1, "a", owners=["Ada", "Bob"]), task(2, "b", "Completed"), task(3, "c"), task(4, "d")]
        got = {(c["id"], c["kind"]): c for c in A.status_changes(old, new, NOW)}
        self.assertEqual(set(got), {("rec1", "owners"), ("rec2", "status"), ("rec4", "new")})
        self.assertEqual(got[("rec1", "owners")]["added"], ["Bob"])
        self.assertEqual((got[("rec2", "status")]["frm"], got[("rec2", "status")]["to"]), ("Ongoing", "Completed"))


class TrackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.saved = (A.CONF, A.CACHE, A.CREATED_FILE, A.notify)
        A.CONF, A.CACHE, A.CREATED_FILE = f"{d}/conf", f"{d}/cache", f"{d}/cache/created-recent.json"
        os.makedirs(A.CONF)
        os.makedirs(A.CACHE)
        self.told = []
        A.notify = lambda title, body: self.told.append(title)
        self.svc = A.SERVICES["larkbase"]

    def tearDown(self):
        A.CONF, A.CACHE, A.CREATED_FILE, A.notify = self.saved
        self.tmp.cleanup()

    def entry(self, tasks, **kw):
        return dict(tasks=tasks, me="Ada", **kw)

    def test_assigned_to_you_is_announced_once(self):
        old = self.entry([task(1, "a")])
        new = self.entry([task(1, "a"), task(2, "Fix login", owners=["Ada"]), task(3, "Not mine", owners=["Bob"])])
        self.svc.track(old, new, NOW)
        self.assertEqual(self.told, ["Assigned to you: Fix login"])
        self.svc.track(dict(new), dict(new, tasks=list(new["tasks"])), NOW + 60)       # the next refresh: nothing new
        self.assertEqual(len(self.told), 1)

    def test_several_assignments_become_one_notification(self):
        new = self.entry([task(1, "a"), task(2, "x"), task(3, "y"), task(4, "z")])
        self.svc.track(self.entry([task(1, "a")]), new, NOW)
        self.assertEqual(self.told, ["3 tasks assigned to you"])

    def test_a_task_you_just_added_yourself_is_not_announced(self):
        A.remember_created("rec2")
        self.svc.track(self.entry([task(1, "a")]), self.entry([task(1, "a"), task(2, "mine")]), NOW)
        self.assertEqual(self.told, [])

    def test_deadlines_close_by_are_announced_once_and_old_ones_never(self):
        tomorrow = NOW + 20 * 3600
        tasks = [task(1, "Ship it", due=tomorrow), task(2, "Ancient", due=NOW - 30 * DAY), task(3, "Later", due=NOW + 9 * DAY),
                 task(4, "Done", "Completed", due=tomorrow), task(5, "Bob's", owners=["Bob"], due=tomorrow)]
        e = self.entry(tasks)
        self.svc.track(self.entry([]), e, NOW)
        self.assertEqual(self.told, ["Due tomorrow: Ship it"])
        self.svc.track(dict(e), dict(e, tasks=tasks), NOW + 600)
        self.assertEqual(len(self.told), 1)

    def test_switched_off_still_remembers_the_history(self):
        with open(f"{A.CONF}/config.json", "w") as f:
            json.dump({"notifications": {"tasks": False}}, f)
        new = self.entry([task(1, "a", "Completed")])
        self.svc.track(self.entry([task(1, "a", "Ongoing")]), new, NOW)
        self.assertEqual(self.told, [])
        self.assertEqual([(c["id"], c["to"]) for c in new["changes"]], [("rec1", "Completed")])

    def test_old_history_is_dropped(self):
        old = self.entry([task(1, "a")], changes=[dict(id="x", at=NOW - 40 * DAY, kind="status", to="Completed", owners=[]),
                                                  dict(id="y", at=NOW - DAY, kind="status", to="Completed", owners=[])])
        new = self.entry([task(1, "a")])
        self.svc.track(old, new, NOW)
        self.assertEqual([c["id"] for c in new["changes"]], ["y"])


class StandupTests(unittest.TestCase):
    def entry(self, **kw):
        tasks = [task(1, "Fix login", "Ongoing", priority="Important", note="waiting for review\nmore"),
                 task(2, "Write docs"), task(3, "Late thing", priority="Important", due=NOW - 5 * DAY),
                 task(4, "Bob's task", "Ongoing", owners=["Bob"])]
        changes = [dict(id="d1", title="Ship the API", kind="status", to="Completed", at=NOW - 5 * 3600, owners=["Ada"],
                        project="SHOP"),
                   dict(id="d2", title="Too old", kind="status", to="Completed", at=NOW - 3 * DAY, owners=["Ada"]),
                   dict(id="d3", title="Not mine", kind="status", to="Completed", at=NOW - 3600, owners=["Bob"])]
        e = dict(me="Ada", tasks=tasks, changes=changes)
        e.update(kw)
        return e

    def test_the_note_has_what_was_done_what_is_on_and_what_is_next(self):
        text = S.build(self.entry(), NOW)
        self.assertIn("Standup · Wed 4 Nov", text)
        done, today, nxt, attn = (text.split(h)[1] for h in ("Done since yesterday", "Today", "Next", "Needs attention"))
        self.assertIn("• Ship the API (SHOP)", text.split("Today")[0])
        self.assertNotIn("Too old", text)
        self.assertNotIn("Not mine", text)
        self.assertIn("• Fix login (SHOP · Backend) — waiting for review", text)
        self.assertNotIn("Bob's task", text)
        self.assertIn("Write docs", text.split("Next")[1])
        self.assertIn("Late thing", text.split("Needs attention")[1])
        self.assertTrue(done and today and nxt and attn)

    def test_nothing_recorded_says_so(self):
        text = S.build(self.entry(changes=[], tasks=[]), NOW)
        self.assertIn("nothing recorded yet", text)
        self.assertIn("nothing marked Ongoing", text)

    def test_on_monday_yesterday_means_friday(self):
        monday = datetime.datetime(2026, 11, 2, 9, 0).timestamp()
        friday_done = [dict(id="f", title="Friday work", kind="status", to="Completed", at=monday - 2.5 * DAY, owners=["Ada"])]
        text = S.build(self.entry(changes=friday_done), monday)
        self.assertIn("Done since Friday", text)
        self.assertIn("Friday work", text)


class StartWorkingTests(unittest.TestCase):
    def test_branch_names(self):
        b = A.LarkBase.branch_name
        self.assertEqual(b("Fix the login — now!", "rec9"), "task/fix-the-login-now")
        self.assertEqual(b("انقل shop للاكونت", "recABC123"), "task/shop")
        self.assertEqual(b("عايزين نصور", "recABC123"), "task/abc123")                 # nothing latin: use the id
        self.assertEqual(b("x" * 90, "rec1", "feature/"), "feature/" + "x" * 40)
        self.assertEqual(b("", "rec77aa"), "task/ec77aa")

    def test_the_most_specific_folder_wins(self):
        with tempfile.TemporaryDirectory() as d:
            api, app, other = f"{d}/api", f"{d}/app", f"{d}/blog"
            for p in (api, app, other):
                os.makedirs(p)
            cfg = {"repos": f"SHOP/Backend = {api}\nSHOP = {app}\nBLOG = {other}\nFlutter = {d}/missing\nbad line"}
            f = A.LarkBase.repo_for
            self.assertEqual(f(cfg, dict(project="SHOP", detail="Backend")), api)
            self.assertEqual(f(cfg, dict(project="SHOP", detail="UI/UX")), app)
            self.assertEqual(f(cfg, dict(project="BLOG", detail="")), other)
            self.assertIsNone(f(cfg, dict(project="DOCS", detail="Flutter")))          # that folder does not exist
            self.assertIsNone(f({}, dict(project="SHOP", detail="Backend")))
            self.assertEqual(f({"repos": f"~/x = {api}\nshop = {app}"}, dict(project="SHOP, BLOG", detail="")), app)


if __name__ == "__main__":
    unittest.main()
