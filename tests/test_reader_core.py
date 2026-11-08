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


class SpreadTests(unittest.TestCase):
    def setUp(self):
        self.lay = R.PageLayout([(100, 200)] * 6, 1.0, gap=10, spread=True)

    def test_the_cover_is_alone_then_pages_go_in_pairs(self):
        self.assertEqual(self.lay.rows, [[0], [1, 2], [3, 4], [5]])
        self.assertEqual(self.lay.tops, [0.0, 210.0, 210.0, 420.0, 420.0, 630.0])
        self.assertEqual(self.lay.total, 630 + 200)

    def test_pages_sit_like_an_open_book(self):
        lay = self.lay
        self.assertEqual(lay.col_w, 210)
        self.assertEqual(lay.xoff[0], 110)                       # the cover: the right-hand page
        self.assertEqual((lay.xoff[1], lay.xoff[2]), (0, 110))   # left page, right page
        self.assertEqual(lay.xoff[5], 0)                         # an odd last page: on the left
        self.assertEqual(R.PageLayout([(100, 200)] * 3, 1.0).xoff, [0.0, 0.0, 0.0])

    def test_finding_and_stepping_work_in_rows(self):
        lay = self.lay
        self.assertEqual([lay.page_at(y) for y in (0, 215, 400, 430, 5000)], [0, 1, 1, 3, 5])
        self.assertEqual(list(lay.visible(205, 500)), [1, 2, 3, 4])
        self.assertEqual([lay.step(p, 1) for p in (0, 1, 2, 5)], [1, 3, 3, 5])
        self.assertEqual([lay.step(p, -1) for p in (0, 2, 3, 5)], [0, 0, 1, 3])
        self.assertEqual(lay.row_pages(2), [1, 2])
        self.assertEqual(lay.anchor(lay.position(3, 0.5)), (3, 0.5))

    def test_a_row_is_as_tall_as_its_taller_page(self):
        lay = R.PageLayout([(100, 200), (100, 200), (100, 300), (100, 200)], 1.0, gap=10, spread=True, cover_alone=False)
        self.assertEqual(lay.rows, [[0, 1], [2, 3]])
        self.assertEqual(lay.tops, [0.0, 0.0, 210.0, 210.0])
        self.assertEqual(lay.total, 210 + 300)


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


class CoverTests(unittest.TestCase):
    def test_a_designed_cover_has_the_size_the_colours_and_the_title(self):
        c = R.design_cover("Dive into Deep Learning", "", "Zhang et al", "ai", seed=0)
        self.assertEqual((c.get_width(), c.get_height()), (300, 400))
        c.flush()
        data, stride = c.get_data(), c.get_stride()
        top_left = (data[2 * 4 * 0 + 2 + 20 * stride + 5 * 4], data[1 + 20 * stride + 5 * 4], data[0 + 20 * stride + 5 * 4])
        self.assertTrue(top_left[2] > top_left[0], top_left)                              # the AI palette starts dark blue
        light = sum(1 for y in range(60, 200, 3) for x in range(30, 270, 3)
                    if min(data[y * stride + x * 4: y * stride + x * 4 + 3]) > 200)
        self.assertGreater(light, 40)                                                      # white title letters are there

    def test_topics_variants_and_titles_change_the_picture(self):
        def pixels(*a, **k):
            s = R.design_cover(*a, **k)
            s.flush()
            return bytes(s.get_data())
        base = pixels("A Book", "", "Me", "ai", seed=0)
        self.assertEqual(base, pixels("A Book", "", "Me", "ai", seed=0))                   # the same book, the same cover
        self.assertNotEqual(base, pixels("A Book", "", "Me", "ai", seed=1))                # another palette
        self.assertNotEqual(base, pixels("A Book", "", "Me", "web", seed=0))
        self.assertNotEqual(base, pixels("Other", "", "Me", "ai", seed=0))
        for topic in R.PALETTES:                                                           # every topic can be drawn
            R.design_cover("T", "sub", "A", topic, seed=3)

    def test_a_very_long_title_still_fits_without_cutting_words(self):
        title = "Internationalization Considerations for Extraordinarily Long Programming Language Documentation"
        c = R.design_cover(title, "A subtitle", "An Author", "other")
        self.assertEqual((c.get_width(), c.get_height()), (300, 400))
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, 10, 10)
        size = R._fit_title(cairo.Context(surf), title, 240, 200)
        self.assertLess(size, 36)
        self.assertGreaterEqual(size, 18)

    def test_a_blank_page_is_told_from_one_with_a_title(self):
        s = cairo.ImageSurface(cairo.FORMAT_RGB24, 120, 160)
        cr = cairo.Context(s)
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        self.assertTrue(R.is_blank(s))
        cr.set_source_rgb(0.1, 0.1, 0.1)
        cr.rectangle(10, 20, 100, 6)                                                       # a line of title: 3% of the page
        cr.fill()
        self.assertFalse(R.is_blank(s))


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
        self.assertEqual(R.job_cover(self.path, dest, width=120, mode="pdf"), ("Test Book", "Ann Author", 8))
        s = cairo.ImageSurface.create_from_png(dest)
        self.assertEqual((s.get_width(), s.get_height()), (120, 180))

    def test_covers_in_the_three_modes(self):
        def make(mode):
            dest = f"{self.tmp.name}/cover-{mode}.png"
            R.job_cover(self.path, dest, mode=mode, fallback_title="Test Book")
            return cairo.ImageSurface.create_from_png(dest)
        designed, pdf, auto = make("designed"), make("pdf"), make("auto")
        self.assertEqual((designed.get_width(), designed.get_height()), (300, 400))
        self.assertEqual((pdf.get_width(), pdf.get_height()), (300, 450))               # the page itself: 400 x 600 points
        self.assertEqual((auto.get_width(), auto.get_height()), (300, 450))              # auto: the book's own first page
        self.assertFalse(R.is_blank(auto))

    def test_a_blank_first_page_gets_a_drawn_cover_in_auto_mode(self):
        blank = f"{self.tmp.name}/blank.pdf"
        s = cairo.PDFSurface(blank, 400, 600)
        s.set_metadata(cairo.PDFMetadata.TITLE, "A Blank Start")
        cr = cairo.Context(s)
        cr.show_page()
        cr.show_page()
        s.finish()
        dest = f"{self.tmp.name}/blank-auto.png"
        R.job_cover(blank, dest, mode="auto", fallback_title="A Blank Start")
        c = cairo.ImageSurface.create_from_png(dest)
        self.assertEqual((c.get_width(), c.get_height()), (300, 400))                   # the drawn cover, not the empty page
        self.assertFalse(R.is_blank(c))
        R.job_cover(blank, dest, mode="pdf")
        self.assertTrue(R.is_blank(cairo.ImageSurface.create_from_png(dest)))             # pdf mode keeps the blank page


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
