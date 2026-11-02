"""Time tracking for tasks: the timer that Start begins, sessions, totals per task and per day, and the report.

    python3 -m unittest discover -s tests
"""
import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
import timelog as T  # noqa: E402

T0 = datetime.datetime(2026, 11, 3, 9, 0).timestamp()
H = 3600


def task(i, title="Fix login"):
    return dict(id=f"rec{i}", title=title, url=f"https://example.test/{i}")


class TimelogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (T.CONF, T.CACHE, T.ACTIVE_FILE, T.LOG_FILE)
        T.CONF, T.CACHE = f"{self.tmp.name}/conf", f"{self.tmp.name}/cache"
        T.ACTIVE_FILE, T.LOG_FILE = f"{T.CACHE}/active-task.json", f"{T.CONF}/time-log.json"

    def tearDown(self):
        T.CONF, T.CACHE, T.ACTIVE_FILE, T.LOG_FILE = self.saved
        self.tmp.cleanup()

    def test_start_runs_a_timer_and_stop_keeps_the_session(self):
        T.start(task(1), repo="/code/api", now=T0)
        self.assertEqual(T.active()["title"], "Fix login")
        self.assertEqual(T.elapsed(T.active(), T0 + 90), 90)
        s = T.stop(now=T0 + 2 * H)
        self.assertEqual((s["id"], s["end"] - s["start"], s["repo"]), ("rec1", 2 * H, "/code/api"))
        self.assertIsNone(T.active())
        self.assertEqual(T.totals()["rec1"], 2 * H)

    def test_starting_another_task_ends_the_first_and_the_same_task_keeps_running(self):
        T.start(task(1), now=T0)
        T.start(task(1), repo="/code/api", branch="task/x", now=T0 + 600)           # same task: nothing restarts
        self.assertEqual((T.active()["started"], T.active()["repo"], T.active()["branch"]), (T0, "/code/api", "task/x"))
        T.start(task(2, "Other"), now=T0 + H)
        self.assertEqual(T.active()["id"], "rec2")
        self.assertEqual(T.totals(T0 + H)["rec1"], H)

    def test_very_short_sessions_are_dropped_and_forgotten_ones_are_cut(self):
        T.start(task(1), now=T0)
        self.assertIsNone(T.stop(now=T0 + 10))
        self.assertEqual(T.totals(T0 + 20), {})
        T.start(task(2), now=T0)
        s = T.stop(now=T0 + 3 * 24 * H)                      # left running for days
        self.assertEqual(s["end"] - s["start"], 12 * H)

    def test_stop_only_stops_the_task_you_name(self):
        T.start(task(1), now=T0)
        self.assertIsNone(T.stop(now=T0 + H, task_id="rec2"))
        self.assertEqual(T.active()["id"], "rec1")
        self.assertIsNotNone(T.stop(now=T0 + H, task_id="rec1"))

    def test_a_running_task_counts_up_to_now_and_a_session_over_midnight_is_split(self):
        night = datetime.datetime(2026, 11, 3, 23, 0).timestamp()
        T.start(task(1), now=night)
        today, tomorrow = datetime.date(2026, 11, 3), datetime.date(2026, 11, 4)
        now = night + 3 * H                                   # 02:00 the next day
        self.assertEqual(T.totals(now)["rec1"], 3 * H)
        self.assertEqual(T.on_day(today, now)["rec1"], 1 * H)
        self.assertEqual(T.on_day(tomorrow, now)["rec1"], 2 * H)

    def test_a_task_completed_somewhere_else_ends_its_timer(self):
        T.start(task(1), now=T0)
        T.sync_with_board([dict(id="rec1", status="Ongoing ")])
        self.assertIsNotNone(T.active())
        T.sync_with_board([dict(id="rec1", status="Completed")])
        self.assertIsNone(T.active())

    def test_formatting(self):
        self.assertEqual([T.fmt(x) for x in (5, 59, 60, 42 * 60, 3600, 3600 + 5 * 60, 26 * 3600)],
                         ["<1m", "<1m", "1m", "42m", "1h 00m", "1h 05m", "26h 00m"])

    def test_text_from_the_board_cannot_drive_the_terminal(self):
        evil = "Fix\x1b[2J\x1b]0;pwned\x07 login\r\nnext"
        T.start(task(1, evil), repo="/code/ap\x1b[31mi", now=T0)
        self.assertNotIn("\x1b", T.active()["title"])
        T.stop(now=T0 + 90 * 60)
        text = T.report("today", T0 + 5 * H)
        for bad in ("\x1b", "\x07", "\r", "\x00"):
            self.assertNotIn(bad, text)
        self.assertIn("Fix", text)
        # a log written by an older version, with raw text in it, is cleaned when it is printed
        import json
        with open(T.LOG_FILE, "w") as f:
            json.dump({"sessions": [dict(id="old", title="\x1b[31mred\x07", repo="/c/\x1b[0mp", start=T0, end=T0 + H)]}, f)
        text = T.report("today", T0 + 5 * H)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)

    def test_the_report_names_tasks_and_projects(self):
        T.start(task(1, "Fix login"), repo="/code/api", now=T0)
        T.stop(now=T0 + 90 * 60)
        T.start(task(2, "Write docs"), repo="/code/web", now=T0 + 2 * H)
        T.stop(now=T0 + 2 * H + 30 * 60)
        text = T.report("today", T0 + 5 * H)
        self.assertIn("Time · today  (2h 00m)", text)
        self.assertIn("1h 30m  api", text)
        self.assertIn("30m  web", text)
        self.assertIn("1h 30m  Fix login", text)
        self.assertIn("last 7 days", T.report("week", T0 + 5 * H))
        self.assertIn("nothing yet", T.report("today", T0 + 9 * 24 * H))


if __name__ == "__main__":
    unittest.main()
