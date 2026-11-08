"""The PDF reader's non-drawing parts: page layout, picking text, night pages, and Poppler in its worker thread.
The Poppler tests make small PDFs with cairo and are skipped without Poppler."""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
import cairo  # noqa: E402
import reader_core as R  # noqa: E402


def make_pdf(path, pages=8, link=True):
    s = cairo.PDFSurface(path, 400, 600)
    s.set_metadata(cairo.PDFMetadata.TITLE, "Test Book")
    s.set_metadata(cairo.PDFMetadata.AUTHOR, "Ann Author")
    cr = cairo.Context(s)
    for i in range(pages):
        if i % 4 == 0:
            s.add_outline(cairo.PDF_OUTLINE_ROOT, f"Chapter {i // 4 + 1}", f"page={i + 1}", 0)
            if i == 4:
                s.add_outline(cairo.PDF_OUTLINE_ROOT, "Second", "page=6", 0)
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.set_source_rgb(0, 0, 0)
        cr.set_font_size(24)
        cr.move_to(40, 80)
        cr.show_text(f"Page {i + 1} heading")
        cr.set_font_size(12)
        for ln in range(10):
            cr.move_to(40, 120 + ln * 20)
            cr.show_text(f"Line {ln} of page {i + 1}: quick brown fox needle{i}")
        if link and i == 0:
            cr.tag_begin(cairo.TAG_LINK, "page=3")
            cr.move_to(40, 400)
            cr.show_text("go to three")
            cr.tag_end(cairo.TAG_LINK)
            cr.tag_begin(cairo.TAG_LINK, "uri='https://example.com/x'")
            cr.move_to(40, 440)
            cr.show_text("a web link")
            cr.tag_end(cairo.TAG_LINK)
        cr.show_page()
    s.finish()


class LayoutTests(unittest.TestCase):
    def test_pages_stack_and_are_found_by_position(self):
        lay = R.PageLayout([(100, 200), (100, 100), (100, 300)], 2.0, gap=10)
        self.assertEqual(lay.tops, [0.0, 410.0, 620.0])
        self.assertEqual(lay.total, 620 + 600)
        self.assertEqual([lay.page_at(y) for y in (-5, 0, 405, 415, 700, 5000)], [0, 0, 0, 1, 2, 2])
        self.assertEqual(list(lay.visible(300, 450)), [0, 1])
        self.assertEqual(list(lay.visible(405, 412)), [1])                       # in the gap: only the next page
        self.assertEqual(lay.anchor(lay.position(2, 0.25)), (2, 0.25))
        self.assertEqual(list(R.PageLayout([], 1.0).visible(0, 10)), [])


class TextTests(unittest.TestCase):
    def setUp(self):
        # "ab cd" on one line, "ef" on the next
        self.text = "ab cd\nef"
        self.chars = [(0, 0, 10, 12), (10, 0, 20, 12), (20, 0, 30, 12), (30, 0, 40, 12), (40, 0, 50, 12), (50, 0, 50, 0),
                      (0, 20, 10, 32), (10, 20, 20, 32)]

    def test_nearest_character(self):
        self.assertEqual(R.char_at(self.chars, 15, 5), 1)
        self.assertEqual(R.char_at(self.chars, 100, 5), 4)                         # right of the line: its last letter
        self.assertEqual(R.char_at(self.chars, 5, 30), 6)
        self.assertEqual(R.char_at(self.chars, 5, 15), 0)                          # closer to the first line
        self.assertIsNone(R.char_at([], 1, 1))

    def test_selection_has_a_box_per_line_without_spaces(self):
        rects = R.selection_rects(self.chars, self.text, 0, 7)
        self.assertEqual(rects, [(0, 0, 50, 12), (0, 20, 20, 32)])                              # words of a line join up
        self.assertEqual(R.selection_rects(self.chars, self.text, 7, 6), [(0, 20, 20, 32)])     # backwards works too
        self.assertEqual(R.selection_rects(self.chars, self.text, 2, 2), [])                    # just a space

    def test_flip_turns_rectangles_over(self):
        self.assertEqual(R.flip((10, 100, 50, 130), 600), (10, 470, 50, 500))


class ThemeTests(unittest.TestCase):
    def pixel(self, surf, x=1, y=1):
        surf.flush()
        b = surf.get_data()
        o = y * surf.get_stride() + x * 4
        return b[o + 2], b[o + 1], b[o]                                              # r, g, b

    def page(self, color):
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, 4, 4)
        cr = cairo.Context(s)
        cr.set_source_rgb(*color)
        cr.paint()
        return s

    def test_night_swaps_white_for_a_soft_dark_and_black_for_soft_light(self):
        white = self.pixel(R.tint(self.page((1, 1, 1)), "night"))
        black = self.pixel(R.tint(self.page((0, 0, 0)), "night"))
        self.assertEqual(white, (0x1b, 0x1a, 0x21))
        self.assertEqual(black, (0xd8, 0xd4, 0xc8))

    def test_sepia_warms_white_and_keeps_black(self):
        self.assertEqual(self.pixel(R.tint(self.page((1, 1, 1)), "sepia")), (0xf1, 0xe4, 0xc3))
        self.assertEqual(self.pixel(R.tint(self.page((0, 0, 0)), "sepia")), (0, 0, 0))
        self.assertEqual(self.pixel(R.tint(self.page((1, 1, 1)), "light")), (255, 255, 255))

    def test_auto_theme_follows_the_clock(self):
        self.assertEqual([R.resolve_theme("auto", h) for h in (3, 6, 12, 18, 19, 23)],
                         ["night", "light", "light", "light", "night", "night"])
        self.assertEqual(R.resolve_theme("sepia", 23), "sepia")
        self.assertEqual(R.resolve_theme("weird", 1), "light")
        self.assertEqual(R.paper_color("night"), "1b1a21")


@unittest.skipUnless(R.Poppler, "needs Poppler (gir1.2-poppler-0.18)")
class PopplerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = f"{cls.tmp.name}/book.pdf"
        make_pdf(cls.path)
        cls.w = R.PdfWorker(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.w.close()
        cls.tmp.cleanup()

    def run_job(self, fn, prio=0):
        """Run a job on the worker thread and wait for its result (the result normally goes to the main loop)."""
        done, box = threading.Event(), []
        orig = R.GLib.idle_add
        R.GLib.idle_add = lambda f, *a: (box.append(a[0] if a else None), done.set(), False)[-1]
        try:
            self.w.submit(prio, fn, lambda r: None)
            self.assertTrue(done.wait(10))
        finally:
            R.GLib.idle_add = orig
        if isinstance(box[0], Exception):
            raise box[0]
        return box[0]

    def test_info_has_sizes_titles_and_a_table_of_contents(self):
        info = self.run_job(R.job_info)
        self.assertEqual((info["pages"], info["title"], info["author"]), (8, "Test Book", "Ann Author"))
        self.assertEqual(info["sizes"][0], (400.0, 600.0))
        self.assertEqual(info["outline"], [(0, "Chapter 1", 0), (0, "Chapter 2", 4), (0, "Second", 5)])

    def test_a_page_is_drawn_to_size_and_themed(self):
        light = self.run_job(lambda d: R.job_render(d, 0, 0.5, "light"))
        night = self.run_job(lambda d: R.job_render(d, 0, 0.5, "night"))
        self.assertEqual((light.get_width(), light.get_height()), (200, 300))
        self.assertEqual(ThemeTests().pixel(light, 2, 2), (255, 255, 255))
        self.assertEqual(ThemeTests().pixel(night, 2, 2), (0x1b, 0x1a, 0x21))

    def test_text_boxes_match_the_text_from_the_top_left(self):
        text, boxes = self.run_job(lambda d: R.job_text(d, 2))
        self.assertTrue(text.startswith("Page 3 heading"))
        self.assertEqual(len(text), len(boxes))
        x1, y1, x2, y2 = boxes[0]
        self.assertTrue(30 < x1 < 50 and y1 < 80 < y2, boxes[0])                    # the heading's baseline is at y = 80
        i = R.char_at(boxes, x1 + 1, (y1 + y2) / 2)
        self.assertEqual(i, 0)
        j = R.char_at(boxes, boxes[3][0] + 1, (y1 + y2) / 2)
        self.assertEqual(text[min(i, j):max(i, j) + 1], "Page")

    def test_search_boxes_are_from_the_top_like_the_text(self):
        hits = self.run_job(lambda d: R.job_find(d, 3, "NEEDLE3"))
        self.assertEqual(len(hits), 10)
        text, boxes = self.run_job(lambda d: R.job_text(d, 3))
        x1, y1, x2, y2 = hits[0]
        self.assertLess(y1, 140)                                                    # the first line is near the top
        k = R.char_at(boxes, (x1 + x2) / 2, (y1 + y2) / 2)
        self.assertIn("needle3", text[max(0, k - 8):k + 8])                         # the box lands on the word itself
        self.assertEqual(self.run_job(lambda d: R.job_find(d, 3, "zzzz")), [])

    @unittest.skipUnless(hasattr(cairo.Context, "tag_begin"), "this cairo cannot write links")
    def test_links_go_to_pages_or_web_addresses(self):
        links = self.run_job(lambda d: R.job_links(d, 0))
        kinds = sorted(k for _b, k in links)
        self.assertEqual(kinds, [("page", 2), ("uri", "https://example.com/x")])
        box = next(b for b, k in links if k[0] == "page")
        self.assertTrue(380 < box[1] < 410, box)                                    # drawn at y = 400, from the top

    def test_cover_and_details_for_the_library(self):
        dest = f"{self.tmp.name}/cover.png"
        self.assertEqual(R.job_cover(self.path, dest, width=120), ("Test Book", "Ann Author", 8))
        s = cairo.ImageSurface.create_from_png(dest)
        self.assertEqual((s.get_width(), s.get_height()), (120, 180))

    def test_a_broken_file_is_reported_not_raised_in_the_window(self):
        bad = f"{self.tmp.name}/bad.pdf"
        with open(bad, "wb") as f:
            f.write(b"%PDF-1.4 nonsense")
        w = R.PdfWorker(bad)
        done, box = threading.Event(), []
        orig = R.GLib.idle_add
        R.GLib.idle_add = lambda f, *a: (box.append(a[0]), done.set(), False)[-1]
        try:
            w.submit(0, R.job_info, lambda r: None)
            self.assertTrue(done.wait(10))
        finally:
            R.GLib.idle_add = orig
            w.close()
        self.assertIsInstance(box[0], Exception)


if __name__ == "__main__":
    unittest.main()
