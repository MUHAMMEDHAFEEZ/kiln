"""Snapping and lining up of rectangles (no GTK needed)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
import snap as S  # noqa: E402

SCREEN = (1920, 1080)


class SnapMoveTests(unittest.TestCase):
    def test_a_widget_sticks_next_to_another_with_the_gap(self):
        other = (100, 100, 300, 200)                                      # right edge at 400
        x, y, guides = S.snap_move((412, 105, 200, 150), [other], SCREEN)  # our left is 6 px from 400 + 18 = 418
        self.assertEqual((x, y), (418, 100))                              # next to it, with the gap, and its top aligned
        self.assertTrue(any(g["orient"] == "h" and g["pos"] == 100 for g in guides))     # a guide at the shared top

    def test_it_sticks_before_it_too(self):
        other = (500, 100, 300, 200)                                      # starts at 500; we end at 500 - 18 = 482
        x, y, _g = S.snap_move((270, 400, 200, 150), [other], SCREEN)     # our right edge would be 470: 12 px off
        self.assertEqual(x, 282)

    def test_edges_and_centres_align(self):
        other = (200, 300, 400, 200)
        x, y, g = S.snap_move((203, 700, 100, 100), [other], SCREEN)      # left edges 3 px apart
        self.assertEqual(x, 200)
        x, y, g = S.snap_move((100, 395, 100, 100), [other], SCREEN)      # our bottom (495) is 5 px from theirs (500)
        self.assertEqual(y, 400)
        x, y, g = S.snap_move((350, 700, 100, 100), [other], SCREEN)      # horizontal centres: ours 400, theirs 400
        self.assertEqual(x, 350)
        x, y, g = S.snap_move((352, 700, 100, 100), [other], SCREEN)
        self.assertEqual(x, 350)                                          # 2 px off: the centres meet

    def test_nothing_near_means_no_change_and_no_guides(self):
        x, y, g = S.snap_move((700, 600, 100, 100), [(100, 100, 100, 100)], SCREEN)
        self.assertEqual((x, y, g), (700, 600, []))

    def test_the_threshold_is_respected(self):
        other = (100, 100, 100, 100)
        self.assertEqual(S.snap_move((100 + 100 + 18 + 14, 500, 50, 50), [other], SCREEN)[0], 218)    # exactly at the threshold: sticks
        self.assertEqual(S.snap_move((100 + 100 + 18 + 15, 500, 50, 50), [other], SCREEN)[0], 233)    # one more pixel: it does not

    def test_the_nearest_target_wins(self):
        others = [(100, 0, 100, 50), (230, 0, 100, 50)]                   # lefts at 100 and 230; rights at 200 and 330
        x, _y, _g = S.snap_move((226, 500, 60, 60), others, SCREEN)      # 4 px from 230; 8 from 218 (next to the first with the gap)
        self.assertEqual(x, 230)

    def test_the_screen_edges_and_centre(self):
        x, y, g = S.snap_move((6, 700, 100, 100), [], SCREEN)
        self.assertEqual(x, 0)
        x, y, g = S.snap_move((1810, 700, 100, 100), [], SCREEN)          # right edge 1910: 10 px from the screen's
        self.assertEqual(x, 1820)
        x, y, g = S.snap_move((912, 500, 100, 100), [], SCREEN)           # centre 962: 2 px from 960
        self.assertEqual(x, 910)
        x, y, g = S.snap_move((100, 100, 100, 100), [], SCREEN, margin=48)
        self.assertEqual((x, y), (100, 100))

    def test_guides_join_the_rectangles(self):
        other = (100, 100, 300, 200)
        _x, _y, guides = S.snap_move((418, 700, 100, 100), [other], SCREEN)    # next to it horizontally, far below
        vertical = [g for g in guides if g["orient"] == "v"]
        self.assertEqual([g["pos"] for g in vertical], [418])               # the line our left edge lands on (their edge + the gap)
        self.assertEqual((vertical[0]["a"], vertical[0]["b"]), (700, 800))    # and it only spans our own rectangle: theirs is not there

    def test_resizing_sticks_the_far_edges(self):
        other = (500, 100, 300, 200)
        w, h, g = S.snap_resize((100, 100, 370, 190), [other], SCREEN)    # our right edge 470 -> 500 - 18 = 482
        self.assertEqual(w, 382)
        self.assertEqual(h, 200)                                          # the bottom meets the other's bottom (300)
        w, h, g = S.snap_resize((100, 100, 200, 100), [other], SCREEN)    # nothing near
        self.assertEqual((w, h, g), (200, 100, []))


class LineUpTests(unittest.TestCase):
    R = {"a": (100, 100, 200, 100), "b": (400, 250, 150, 300), "c": (700, 180, 100, 120)}

    def test_align(self):
        self.assertEqual(S.align(self.R, "left"), {"a": (100, 100), "b": (100, 250), "c": (100, 180)})
        self.assertEqual(S.align(self.R, "right"), {"a": (600, 100), "b": (650, 250), "c": (700, 180)})
        self.assertEqual(S.align(self.R, "top"), {"a": (100, 100), "b": (400, 100), "c": (700, 100)})
        self.assertEqual(S.align(self.R, "bottom"), {"a": (100, 450), "b": (400, 250), "c": (700, 430)})
        self.assertEqual(S.align(self.R, "hcenter")["a"], (350, 100))     # the middle of the bounding box (100..800)
        self.assertEqual(S.align(self.R, "vcenter")["b"], (400, 175))     # the middle of the bounding box (100..550)
        with self.assertRaises(ValueError):
            S.align(self.R, "diagonal")
        self.assertEqual(S.align({"a": (1, 2, 3, 4)}, "left"), {"a": (1, 2)})           # one rectangle: nothing to align to

    def test_distribute_gives_equal_space(self):
        r = {"a": (0, 0, 100, 50), "b": (150, 0, 100, 50), "c": (700, 0, 100, 50)}
        out = S.distribute(r, "h")
        self.assertEqual(out, {"a": (0, 0), "b": (350, 0), "c": (700, 0)})       # (800 - 300) / 2 = 250 between each
        r2 = {"a": (0, 0, 50, 100), "b": (0, 30, 50, 100), "c": (0, 500, 50, 100)}
        out = S.distribute(r2, "v")
        self.assertEqual([out[k][1] for k in "abc"], [0, 250, 500])
        self.assertEqual(S.distribute({"a": (0, 0, 10, 10), "b": (50, 0, 10, 10)}, "h"), {"a": (0, 0), "b": (50, 0)})  # two: nothing to do

    def test_line_up_one_after_the_other(self):
        out = S.line_up(self.R, "row", gap=18)
        self.assertEqual(out, {"a": (100, 100), "b": (318, 100), "c": (486, 100)})
        out = S.line_up(self.R, "column", gap=10)
        self.assertEqual(out["a"], (100, 100))                              # the first by top stays; the others follow below it
        self.assertEqual(out["c"], (100, 210))
        self.assertEqual(out["b"], (100, 340))
        with self.assertRaises(ValueError):
            S.line_up(self.R, "diagonal")

    def test_the_arguments_are_left_alone(self):
        copy = dict(self.R)
        S.align(self.R, "left")
        S.distribute(self.R, "h")
        S.line_up(self.R, "row")
        self.assertEqual(self.R, copy)


if __name__ == "__main__":
    unittest.main()
