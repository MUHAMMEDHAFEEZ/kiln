"""The PDF library: files, progress, bookmarks, highlights, notes and reading statistics (no GTK, no Poppler needed)."""
import datetime
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
import books as B  # noqa: E402

DAY = 86400


def fake_pdf(path, body=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4\n" + body)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.store = B.Store(f"{self.dir}/books.json")


class FilesTests(Base):
    def test_config_has_defaults_and_checks_values(self):
        saved = B.CONF
        self.addCleanup(lambda: setattr(B, "CONF", saved))
        B.CONF = self.dir
        self.assertEqual(B.config()["theme"], "auto")
        self.assertEqual(B.config()["dirs"], [os.path.expanduser("~/Books")])
        with open(f"{self.dir}/config.json", "w") as f:
            json.dump({"books": {"dirs": "~/Docs/b", "theme": "neon", "daily_goal_minutes": "oops",
                                 "eye_break_minutes": 9999, "other": 1}}, f)
        c = B.config()
        self.assertEqual(c["dirs"], [os.path.expanduser("~/Docs/b")])
        self.assertEqual((c["theme"], c["daily_goal_minutes"], c["eye_break_minutes"]), ("auto", 20, 240))
        self.assertNotIn("other", c)

    def test_scan_finds_pdfs_only_once_and_skips_hidden_and_linked_folders(self):
        fake_pdf(f"{self.dir}/b/one.pdf")
        fake_pdf(f"{self.dir}/b/sub/Two.PDF", b"y")
        fake_pdf(f"{self.dir}/b/.hidden/three.pdf")
        fake_pdf(f"{self.dir}/b/.dot.pdf")
        fake_pdf(f"{self.dir}/elsewhere/four.pdf")
        open(f"{self.dir}/b/notes.txt", "w").close()
        os.symlink(f"{self.dir}/elsewhere", f"{self.dir}/b/linked-folder")
        os.symlink(f"{self.dir}/b/one.pdf", f"{self.dir}/b/same-file.pdf")
        got = [os.path.relpath(f["path"], f"{self.dir}/b") for f in B.scan([f"{self.dir}/b"])]
        self.assertEqual(sorted(got), ["one.pdf", "sub/Two.PDF"])

    def test_the_id_survives_a_rename_but_not_a_different_book(self):
        a = fake_pdf(f"{self.dir}/a.pdf", b"same")
        id_a = B.book_id(a)
        os.rename(a, f"{self.dir}/renamed.pdf")
        self.assertEqual(B.book_id(f"{self.dir}/renamed.pdf"), id_a)
        self.assertNotEqual(B.book_id(fake_pdf(f"{self.dir}/b.pdf", b"other")), id_a)

    def test_add_book_copies_pdfs_never_overwrites_and_refuses_other_files(self):
        src = fake_pdf(f"{self.dir}/in/My Book.pdf")
        dest = f"{self.dir}/Books"
        first = B.add_book(src, dest)
        second = B.add_book(src, dest)
        self.assertEqual(os.path.basename(first), "My Book.pdf")
        self.assertEqual(os.path.basename(second), "My Book (2).pdf")
        txt = f"{self.dir}/in/fake.pdf"
        with open(txt, "w") as f:
            f.write("just text")
        with self.assertRaises(ValueError):
            B.add_book(txt, dest)
        with self.assertRaises(ValueError):
            B.add_book(f"{self.dir}/missing.pdf", dest)

    def test_title_from_the_file_name(self):
        self.assertEqual(B.title_from_name("/x/Clean_Code-by_Martin.pdf"), "Clean Code-by Martin")
        self.assertEqual(B.title_from_name("/x/.pdf"), "Untitled")

    def test_index_remembers_a_file_until_it_changes(self):
        f = fake_pdf(f"{self.dir}/b/one.pdf")
        info = B.scan([f"{self.dir}/b"])[0]
        idx = B.Index(f"{self.dir}/index.json")
        self.assertIsNone(idx.get(info))
        idx.put(info, "A Title\x1b[31m", "Someone", 321)
        idx.save()
        again = B.Index(f"{self.dir}/index.json")
        self.assertEqual(again.get(info)["pages"], 321)
        self.assertNotIn("\x1b", again.get(info)["title"])
        fake_pdf(f, b"longer than before")
        self.assertIsNone(again.get(B.scan([f"{self.dir}/b"])[0]))


class StoreTests(Base):
    def test_position_and_finishing(self):
        self.store.set_position("a", 10, 0.5, pages=100)
        self.assertEqual(self.store.status("a"), "unread")                    # never opened yet
        self.store.opened("a")
        self.assertEqual(self.store.status("a"), "reading")
        self.assertAlmostEqual(self.store.progress("a"), 0.105)
        self.store.set_position("a", 98, 0.2, pages=100)
        self.assertEqual(self.store.status("a"), "finished")
        self.assertEqual(self.store.progress("a"), 1.0)
        self.store.set_position("a", 3, 0.0)                                   # going back does not undo it
        self.assertEqual(self.store.status("a"), "finished")
        self.store.mark_unread("a")
        self.assertEqual((self.store.status("a"), self.store.progress("a")), ("unread", 0.0))
        self.store.mark_finished("a")
        self.assertEqual(self.store.status("a"), "finished")

    def test_a_new_book_has_every_field(self):
        b = self.store.book("new")
        self.assertEqual(set(b), set(B.Store.FIELDS))
        self.assertIsNone(b["zoom"])
        self.assertEqual((b["bookmarks"], b["highlights"], b["notes"]), ([], [], []))
        self.store.set_zoom("new", 1.5)
        self.assertEqual(self.store.book("new")["zoom"], 1.5)
        self.store.set_zoom("new", None)
        self.assertIsNone(self.store.book("new")["zoom"])

    def test_spread_is_a_yes_or_no_choice(self):
        saved = B.CONF
        self.addCleanup(lambda: setattr(B, "CONF", saved))
        B.CONF = self.dir
        self.assertFalse(B.config()["spread"])
        with open(f"{self.dir}/config.json", "w") as f:
            json.dump({"books": {"spread": "yes"}}, f)
        self.assertFalse(B.config()["spread"])                                       # only true counts
        with open(f"{self.dir}/config.json", "w") as f:
            json.dump({"books": {"spread": True}}, f)
        self.assertTrue(B.config()["spread"])
        self.store.set_pref("spread", "maybe")
        self.assertTrue(self.store.pref("spread", True))                              # a wrong value falls back
        self.store.set_pref("spread", False)
        self.assertFalse(self.store.pref("spread", True))

    def test_choices_made_in_the_reader_are_kept(self):
        self.store.set_pref("theme", "night")
        self.store.flush()
        self.assertEqual(B.Store(self.store.path).pref("theme", "light"), "night")
        self.store.set_pref("theme", "plaid")
        self.assertEqual(self.store.pref("theme", "light"), "light")                  # a value that is not a theme

    def test_position_is_kept_in_range(self):
        self.store.set_position("a", 5000, 7.0, pages=10)
        b = self.store.book("a")
        self.assertEqual((b["page"], b["frac"]), (9, 1.0))
        self.store.set_position("a", float("nan"), float("inf"), pages=10)
        self.assertEqual(self.store.book("a")["page"], 0)

    def test_bookmarks_toggle_and_stay_sorted(self):
        for p in (9, 2, 5):
            self.assertTrue(self.store.toggle_bookmark("a", p))
        self.assertEqual(self.store.bookmarks("a"), [2, 5, 9])
        self.assertFalse(self.store.toggle_bookmark("a", 5))
        self.assertEqual(self.store.bookmarks("a"), [2, 9])

    def test_highlights_notes_and_their_checks(self):
        h = self.store.add_highlight("a", 3, [[10, 20, 100, 32], [10, 34, 60, 46]], "  some \n text ", "green")
        self.assertEqual(self.store.highlights("a", 3)[0]["text"], "some text")
        self.assertEqual(self.store.highlights("a", 4), [])
        self.assertIsNone(self.store.add_highlight("a", 3, [], "nothing"))
        self.assertIsNone(self.store.add_highlight("a", 3, [["x", 1, 2, 3], [float("nan")] * 4], "bad rects"))
        self.assertEqual(self.store.highlights("a")[0]["rects"][0], [10, 20, 100, 32])
        self.assertEqual(self.store.add_highlight("a", 1, [[1, 1, 2, 2]], "x", "no-such-colour") and
                         self.store.highlights("a", 1)[0]["color"], "yellow")
        self.assertTrue(self.store.set_highlight_note("a", h, "check this later"))
        self.assertTrue(self.store.set_highlight_color("a", h, "blue"))
        self.assertFalse(self.store.set_highlight_color("a", h, "plaid"))
        self.assertEqual((self.store.highlights("a", 3)[0]["note"], self.store.highlights("a", 3)[0]["color"]),
                         ("check this later", "blue"))
        self.assertTrue(self.store.remove_highlight("a", h))
        self.assertFalse(self.store.remove_highlight("a", h))
        n = self.store.add_note("a", 7, "  why? ")
        self.assertIsNone(self.store.add_note("a", 7, "   "))
        self.assertEqual(self.store.notes("a")[0]["text"], "why?")
        self.assertTrue(self.store.edit_note("a", n, "because"))
        self.assertEqual(self.store.notes("a")[0]["text"], "because")
        self.assertTrue(self.store.remove_note("a", n))
        self.assertEqual(self.store.notes("a"), [])

    def test_ids_are_not_reused(self):
        one = self.store.add_highlight("a", 0, [[0, 0, 1, 1]], "1")
        two = self.store.add_highlight("a", 0, [[0, 0, 1, 1]], "2")
        self.store.remove_highlight("a", one)
        three = self.store.add_highlight("a", 0, [[0, 0, 1, 1]], "3")
        self.assertEqual((one, two, three), (1, 2, 3))

    def test_save_is_private_and_reloads(self):
        self.store.opened("a")
        self.store.set_position("a", 4, 0.25, pages=40)
        self.store.toggle_bookmark("a", 4)
        self.assertTrue(self.store.dirty)
        self.store.flush()
        self.assertFalse(self.store.dirty)
        self.assertEqual(stat.S_IMODE(os.stat(self.store.path).st_mode), 0o600)
        again = B.Store(self.store.path)
        self.assertEqual((again.book("a")["page"], again.bookmarks("a"), again.status("a")), (4, [4], "reading"))

    def test_a_file_edited_by_hand_cannot_break_it(self):
        with open(self.store.path, "w") as f:
            json.dump({"books": {"a": {"page": "three", "bookmarks": "x", "highlights": [1, None, {"page": 2}],
                                       "finished": 1, "notes": {}}, "b": 5}, "log": {"2026-01-01": "oops"}}, f)
        s = B.Store(self.store.path)
        self.assertEqual(s.book("a")["page"], 0)
        self.assertEqual(s.bookmarks("a"), [])
        self.assertEqual(s.highlights("a"), [{"page": 2}])
        self.assertFalse(s.book("a")["finished"])
        self.assertEqual(s.book("b")["pages"], 0)
        self.assertEqual(s.secs_on("2026-01-01"), 0)
        s.add_time("a", 30)                                                    # and it keeps working
        with open(self.store.path, "w") as f:
            f.write("not json")
        self.assertEqual(B.Store(self.store.path).books, {})


class StatsTests(Base):
    def at(self, days_ago, hour=12):
        d = datetime.date.today() - datetime.timedelta(days=days_ago)
        return datetime.datetime.combine(d, datetime.time(hour)).timestamp()

    def test_today_week_and_pages(self):
        self.store.add_time("a", 600, self.at(0))
        self.store.add_time("b", 300, self.at(0, 18))
        self.store.add_time("a", 1200, self.at(2))
        self.assertTrue(self.store.add_page("a", 4, self.at(0)))
        self.assertFalse(self.store.add_page("a", 4, self.at(0)))              # the same page twice counts once
        self.store.add_page("b", 4, self.at(0))
        self.assertEqual(self.store.today(self.at(0)), (900, 2))
        week = self.store.week(self.at(0))
        self.assertEqual([s for _d, s in week], [0, 0, 0, 0, 1200, 0, 900])
        self.assertEqual(week[-1][0], datetime.date.today())
        self.assertEqual(self.store.book_secs("a"), 1800)

    def test_streak_counts_days_in_a_row_and_forgives_a_day_not_read_yet(self):
        for ago in (1, 2, 3, 5):
            self.store.add_time("a", 120, self.at(ago))
        self.assertEqual(self.store.streak(self.at(0)), 3)                      # today empty, yesterday back to 3 days
        self.store.add_time("a", 30, self.at(0))                                # under a minute: not a day yet
        self.assertEqual(self.store.streak(self.at(0)), 3)
        self.store.add_time("a", 40, self.at(0))
        self.assertEqual(self.store.streak(self.at(0)), 4)

    def test_time_left_needs_some_history(self):
        self.store.opened("a")
        self.store.set_position("a", 19, 0.0, pages=120)
        self.assertIsNone(self.store.minutes_left("a"))
        for p in range(10, 20):
            self.store.add_page("a", p, self.at(0))
        self.store.add_time("a", 1000, self.at(0))                              # 100 s a page, 100 pages to go
        self.assertEqual(self.store.minutes_left("a"), round(100 * 100 / 60))

    def test_old_days_are_dropped(self):
        self.store.add_time("a", 100, self.at(B.KEEP_DAYS + 30))
        self.store.add_time("a", 100, self.at(1))
        self.store.prune(self.at(0))
        self.assertEqual(len(self.store.log), 1)

    def test_tracker_counts_active_time_pages_and_breaks(self):
        t0 = 1_700_000_000.0
        tr = B.Tracker(self.store)
        tr.poke(t0)
        counted = [tr.tick(t0 + i * 2, "a", 3) for i in range(0, 6)]          # 10 s on one page
        self.assertEqual(counted.count(True), 1)
        self.assertGreaterEqual(self.store.book_secs("a"), 8)
        self.assertEqual(self.store.pages_seen("a"), 1)
        tr.tick(t0 + 12, "a", 4)                                               # a new page: not counted until it stays
        self.assertEqual(self.store.pages_seen("a"), 1)
        secs = self.store.book_secs("a")
        tr.tick(t0 + 12 + B.IDLE_SECS + 50, "a", 4)                            # nobody touched anything for a long time
        self.assertEqual(self.store.book_secs("a"), secs)
        tr2 = B.Tracker(B.Store(f"{self.dir}/other.json"))
        tr2.poke(t0)
        for i in range(0, 1800, 2):                                         # 30 min of reading, scrolling now and then
            if i % 60 == 0:
                tr2.poke(t0 + i)
            tr2.tick(t0 + i, "a", i // 120)
        self.assertTrue(tr2.break_due(t0 + 1800, 25))
        self.assertFalse(tr2.break_due(t0 + 1805, 25))                   # asked once; the count starts again
        self.assertFalse(tr2.break_due(t0 + 1810, 0))                   # 0 turns reminders off


class LibraryTests(Base):
    def setUp(self):
        super().setUp()
        files = [fake_pdf(f"{self.dir}/b/{n}.pdf", n.encode()) for n in ("alpha", "Beta_book", "gamma")]
        self.files = B.scan([f"{self.dir}/b"])
        self.index = B.Index(f"{self.dir}/index.json")
        by = {os.path.basename(f["path"]): f for f in self.files}
        self.index.put(by["alpha.pdf"], "Alpha: An Introduction", "Ann Author", 200)
        ids = {os.path.basename(f["path"]): B.book_id(f["path"], f["size"]) for f in self.files}
        self.store.opened(ids["alpha.pdf"], 500)
        self.store.set_position(ids["alpha.pdf"], 50, 0.0, pages=200)
        self.store.opened(ids["gamma.pdf"], 900)
        self.store.set_position(ids["gamma.pdf"], 30, 0.0, pages=40)
        self.store.mark_finished(ids["gamma.pdf"])
        self.ids = ids
        self.entries = B.library(self.store, self.index, self.files, now=100)

    def test_entries_use_the_pdf_title_or_the_file_name(self):
        titles = {e["id"]: e["title"] for e in self.entries}
        self.assertEqual(titles[self.ids["alpha.pdf"]], "Alpha: An Introduction")
        self.assertEqual(titles[self.ids["Beta_book.pdf"]], "Beta book")
        e = next(e for e in self.entries if e["id"] == self.ids["alpha.pdf"])
        self.assertEqual((e["status"], e["pages"], round(e["progress"], 2)), ("reading", 200, 0.25))

    def test_filter_and_sort(self):
        names = lambda es: [os.path.basename(e["path"]) for e in es]  # noqa: E731
        self.assertEqual(names(B.arrange(self.entries, sort="recent"))[:2], ["gamma.pdf", "alpha.pdf"])
        self.assertEqual(names(B.arrange(self.entries, sort="title")), ["alpha.pdf", "Beta_book.pdf", "gamma.pdf"])
        self.assertEqual(names(B.arrange(self.entries, status="finished")), ["gamma.pdf"])
        self.assertEqual(names(B.arrange(self.entries, status="unread")), ["Beta_book.pdf"])
        self.assertEqual(names(B.arrange(self.entries, query="ann intro")), ["alpha.pdf"])
        self.assertEqual(names(B.arrange(self.entries, query="zzz")), [])
        self.assertEqual(names(B.arrange(self.entries, sort="progress"))[0], "gamma.pdf")

    def test_continue_reading_is_only_what_is_in_progress(self):
        self.assertEqual([os.path.basename(e["path"]) for e in B.continue_reading(self.entries)], ["alpha.pdf"])

    def test_markdown_export(self):
        bid = self.ids["alpha.pdf"]
        h = self.store.add_highlight(bid, 4, [[1, 1, 5, 5]], "a key idea")
        self.store.set_highlight_note(bid, h, "use in the talk")
        self.store.add_note(bid, 1, "start here")
        md = B.export_markdown(next(e for e in self.entries if e["id"] == bid), self.store)
        self.assertIn("# Alpha: An Introduction", md)
        self.assertLess(md.index("p. 2"), md.index("p. 5"))
        self.assertIn("> a key idea", md)
        self.assertIn("note: use in the talk", md)
        empty = B.export_markdown(next(e for e in self.entries if e["id"] == self.ids["Beta_book.pdf"]), self.store)
        self.assertIn("no highlights", empty)

    def test_export_replaces_a_planted_link_instead_of_following_it(self):
        bid = self.ids["alpha.pdf"]
        self.store.add_note(bid, 0, "hello")
        e = next(e for e in self.entries if e["id"] == bid)
        target = f"{self.dir}/precious.txt"
        with open(target, "w") as f:
            f.write("keep me")
        out = os.path.splitext(e["path"])[0] + ".notes.md"
        os.symlink(target, out)
        self.assertEqual(B.write_export(e, self.store), out)
        self.assertFalse(os.path.islink(out))
        self.assertIn("hello", open(out).read())
        self.assertEqual(open(target).read(), "keep me")
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        self.assertEqual([n for n in os.listdir(os.path.dirname(out)) if n.endswith(".tmp")], [])

    def test_minutes_format(self):
        self.assertEqual([B.fmt_minutes(m) for m in (0.2, 42, 65)], ["<1m", "42m", "1h 05m"])


if __name__ == "__main__":
    unittest.main()
