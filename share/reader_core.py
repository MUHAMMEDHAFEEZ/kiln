"""What the PDF reader does without drawing anything: page layout, picking text with the mouse, night and sepia pages,
and the worker thread that owns the Poppler document.

Poppler is only ever touched from that one thread (render, text, search, links), so the window never waits for a page.
Coordinates are PDF points with the origin at the top left of the page, which is what Poppler's text functions use;
`find_text` gives them from the bottom, so they are turned over here.
"""
import bisect
import itertools
import os
import queue
import threading

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

try:
    gi.require_version("Poppler", "0.18")
    from gi.repository import Poppler  # noqa: E402
except (ValueError, ImportError):
    Poppler = None

PAPER = {"light": None, "sepia": "f1e4c3", "night": ("1b1a21", "d8d4c8")}      # night: (page colour, text colour)
NIGHT_HOURS = (19, 6)                                                           # "auto" is night from 19:00 to 06:00


def resolve_theme(theme, hour):
    if theme != "auto":
        return theme if theme in PAPER else "light"
    return "night" if hour >= NIGHT_HOURS[0] or hour < NIGHT_HOURS[1] else "light"


def _hex(h):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def tint(surface, theme):
    """Turn a rendered white page into a sepia or a soft dark one (in place). Night inverts the page, then keeps the
    darkest pixel at the page colour and the lightest at the text colour, so it is never pure black on pure white."""
    spec = PAPER.get(theme)
    if not spec:
        return surface
    cr = cairo.Context(surface)
    w, h = surface.get_width(), surface.get_height()
    if theme == "sepia":
        cr.set_operator(cairo.OPERATOR_MULTIPLY)
        cr.set_source_rgb(*_hex(spec))
        cr.paint()
        return surface
    bg, fg = spec
    cr.set_operator(cairo.OPERATOR_DIFFERENCE)
    cr.set_source_rgb(1, 1, 1)
    cr.rectangle(0, 0, w, h)
    cr.fill()
    cr.set_operator(cairo.OPERATOR_LIGHTEN)
    cr.set_source_rgb(*_hex(bg))
    cr.rectangle(0, 0, w, h)
    cr.fill()
    cr.set_operator(cairo.OPERATOR_DARKEN)
    cr.set_source_rgb(*_hex(fg))
    cr.rectangle(0, 0, w, h)
    cr.fill()
    return surface


def paper_color(theme):
    """The colour of an empty page of this theme (shown while the page is still being drawn)."""
    spec = PAPER.get(theme)
    return "ffffff" if not spec else spec if isinstance(spec, str) else spec[0]


# ------------------------------------------------------------------ pages in a column
class PageLayout:
    """Pages in rows going down: one page a row, or (spread) two side by side like an open book, with the cover on its
    own at the right. `scale` is pixels per point. Every page has the top of its row (`tops`) and its distance from the
    left edge of the column (`xoff`); `col_w` is the width of the whole column."""
    HGAP = 10                                                 # between the two pages of a spread

    def __init__(self, sizes, scale, gap=14, spread=False, cover_alone=True):
        self.sizes, self.scale, self.gap, self.spread = sizes, scale, gap, spread
        n = len(sizes)
        if spread:
            rows = [[0]] if cover_alone and n else []
            rows += [list(range(i, min(i + 2, n))) for i in range(len(rows), n, 2)]
        else:
            rows = [[i] for i in range(n)]
        maxw = max((w for w, _h in sizes), default=0.0) * scale
        self.rows, self.row_of, self.tops, self.xoff, self.row_tops, self.row_h = rows, [0] * n, [0.0] * n, [0.0] * n, [], []
        y = 0.0
        for r, pages in enumerate(rows):
            self.row_tops.append(y)
            rh = max(sizes[p][1] for p in pages) * scale
            self.row_h.append(rh)
            for k, p in enumerate(pages):
                w = sizes[p][0] * scale
                self.row_of[p], self.tops[p] = r, y
                if not spread:
                    self.xoff[p] = (maxw - w) / 2
                elif len(pages) == 2:
                    self.xoff[p] = maxw - w if k == 0 else maxw + self.HGAP
                elif pages[0] == 0 and cover_alone:
                    self.xoff[p] = maxw + self.HGAP               # the cover alone: the right-hand page of the first spread
                else:
                    self.xoff[p] = maxw - w                       # an odd last page: the left-hand one
            y += rh + gap
        self.total = max(0.0, y - gap)
        self.col_w = 2 * maxw + self.HGAP if spread else maxw

    def width(self, i):
        return self.sizes[i][0] * self.scale

    def height(self, i):
        return self.sizes[i][1] * self.scale

    def _row_at(self, y):
        return max(0, min(len(self.rows) - 1, bisect.bisect_right(self.row_tops, y) - 1))

    def page_at(self, y):
        """The page that holds column position y (the first one of a spread; the nearest row in gaps and outside)."""
        return self.rows[self._row_at(y)][0] if self.rows else 0

    def visible(self, y0, y1):
        if not self.rows:
            return range(0)
        first, last = self._row_at(y0), self._row_at(y1)
        while first < last and self.row_tops[first] + self.row_h[first] < y0:
            first += 1
        return range(self.rows[first][0], self.rows[last][-1] + 1)

    def step(self, page, d):
        """The first page of the row `d` rows after (or before) the row of `page`."""
        if not self.rows:
            return 0
        return self.rows[max(0, min(len(self.rows) - 1, self.row_of[max(0, min(len(self.tops) - 1, page))] + d))][0]

    def row_pages(self, page):
        return self.rows[self.row_of[max(0, min(len(self.tops) - 1, page))]] if self.rows else []

    def position(self, page, frac):
        page = max(0, min(len(self.tops) - 1, page))
        return self.tops[page] + frac * self.height(page)

    def anchor(self, y):
        """(page, how far down it, 0..1) of column position y"""
        p = self.page_at(y)
        h = self.height(p) or 1
        return p, max(0.0, min(1.0, (y - self.tops[p]) / h))


# ------------------------------------------------------------------ choosing text
def char_at(chars, x, y):
    """The index of the character closest to a point (all in page points); a character under the point wins."""
    best, best_d = None, None
    for i, (x1, y1, x2, y2) in enumerate(chars):
        if x2 <= x1 or y2 <= y1:
            continue
        dx = max(x1 - x, 0.0, x - x2)
        dy = max(y1 - y, 0.0, y - y2)
        d = dx * dx + 4.0 * dy * dy
        if best_d is None or d < best_d:
            best, best_d = i, d
            if d == 0.0:
                break
    return best


def selection_rects(chars, text, i, j):
    """Boxes (one per line of text) around characters i..j, spaces and line ends left out."""
    lo, hi = min(i, j), max(i, j)
    lines = []
    for k in range(lo, min(hi, len(chars) - 1, len(text) - 1) + 1):
        if text[k].isspace():
            continue
        x1, y1, x2, y2 = chars[k]
        if x2 <= x1 or y2 <= y1:
            continue
        cur = lines[-1] if lines else None
        if cur:
            overlap = min(cur[3], y2) - max(cur[1], y1)
            gap = max(0.0, x1 - cur[2], cur[0] - x2)
            if overlap > 0.5 * min(cur[3] - cur[1], y2 - y1) and gap <= 4 * (y2 - y1):
                cur[0], cur[1], cur[2], cur[3] = min(cur[0], x1), min(cur[1], y1), max(cur[2], x2), max(cur[3], y2)
                continue
        lines.append([x1, y1, x2, y2])
    return [tuple(r) for r in lines]


def flip(rect, page_h):
    """A rectangle measured from the bottom of the page, measured from the top instead."""
    x1, y1, x2, y2 = rect
    return (x1, page_h - y2, x2, page_h - y1)


# ------------------------------------------------------------------ the worker thread
class PdfWorker:
    """Owns the Poppler document in a thread of its own. Jobs run in priority order (0 first: the pages in view);
    each job is `fn(doc)` and its result goes to `done(result)` on the main loop (an exception is passed as a result)."""
    VISIBLE, TEXT, AHEAD, SEARCH = 0, 1, 2, 3

    def __init__(self, path):
        self.path = path
        self.doc = None
        self.q = queue.PriorityQueue()
        self.seq = itertools.count()
        self.closed = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="kiln-pdf")
        self.thread.start()

    def submit(self, prio, fn, done=None):
        if not self.closed:
            self.q.put((prio, next(self.seq), fn, done))

    def _run(self):
        try:
            self.doc = Poppler.Document.new_from_file(GLib.filename_to_uri(self.path, None), None)
            err = None
        except Exception as exc:                                    # a password, or a broken file
            err = exc
        while True:
            _prio, _n, fn, done = self.q.get()
            if fn is None:
                return
            try:
                res = err if err else fn(self.doc)
            except Exception as exc:
                res = exc
            if done and not self.closed:
                GLib.idle_add(done, res)

    def close(self):
        self.closed = True
        self.q.put((-2, next(self.seq), None, None))


# ---- the jobs (each runs in the worker thread and gets the document)
def job_info(doc):
    """Page sizes, title, author and the table of contents."""
    n = doc.get_n_pages()
    sizes = []
    for i in range(n):
        s = doc.get_page(i).get_size()
        sizes.append((s.width, s.height))
    return dict(pages=n, sizes=sizes, title=doc.get_title() or "", author=doc.get_author() or "", outline=outline(doc))


def outline(doc, limit=4000):
    """[(level, title, page from 0)] of the table of contents, in order."""
    out = []
    root = Poppler.IndexIter.new(doc)
    if root is None:
        return out

    def walk(it, level):
        while it and len(out) < limit:
            act = it.get_action()
            page = dest_page(doc, act)
            title = getattr(getattr(act, "goto_dest", None), "title", None) or getattr(getattr(act, "any", None), "title", "") or ""
            if title:
                out.append((level, title.strip(), page))
            child = it.get_child()
            if child is not None:
                walk(child, level + 1)
            if not it.next():
                break
    walk(root, 0)
    return out


def dest_page(doc, act):
    """The page (from 0) an action goes to, or None."""
    if act is None or act.type != Poppler.ActionType.GOTO_DEST:
        return None
    dest = act.goto_dest.dest
    if dest is None:
        return None
    if dest.type == Poppler.DestType.NAMED:
        dest = doc.find_dest(dest.named_dest)
        if dest is None:
            return None
    n = dest.page_num
    return n - 1 if n and n > 0 else None


def job_render(doc, page, px_per_pt, theme):
    """The page drawn at `px_per_pt` device pixels per point (the theme already applied)."""
    p = doc.get_page(page)
    w, h = p.get_size()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, round(w * px_per_pt)), max(1, round(h * px_per_pt)))
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.scale(surf.get_width() / w, surf.get_height() / h)
    p.render(cr)
    return tint(surf, theme)


def job_text(doc, page):
    """(text, one box per character) of a page, top-left origin"""
    p = doc.get_page(page)
    text = p.get_text() or ""
    ok, rects = p.get_text_layout()
    boxes = [(r.x1, r.y1, r.x2, r.y2) for r in rects] if ok else []
    return text, boxes


def job_links(doc, page):
    """[(box, ("page", n) | ("uri", url))] of the links on a page"""
    p = doc.get_page(page)
    h = p.get_size().height
    out = []
    for m in p.get_link_mapping():
        a = m.action
        box = flip((m.area.x1, m.area.y1, m.area.x2, m.area.y2), h)
        if a.type == Poppler.ActionType.GOTO_DEST:
            n = dest_page(doc, a)
            if n is not None:
                out.append((box, ("page", n)))
        elif a.type == Poppler.ActionType.URI and a.uri.uri:
            out.append((box, ("uri", a.uri.uri)))
    return out


def job_find(doc, page, query):
    """[box] of every place `query` appears on a page (ignoring case), top-left origin"""
    p = doc.get_page(page)
    h = p.get_size().height
    return [flip((r.x1, r.y1, r.x2, r.y2), h)
            for r in p.find_text_with_options(query, Poppler.FindFlags.DEFAULT)]


def job_cover(path, dest, width=300):
    """Title, author, page count of a book and its first page saved as a small PNG (for the library)."""
    doc = Poppler.Document.new_from_file(GLib.filename_to_uri(path, None), None)
    n = doc.get_n_pages()
    if n and dest:
        p = doc.get_page(0)
        w, h = p.get_size()
        k = width / w
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, width, max(1, round(h * k)))
        cr = cairo.Context(surf)
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.scale(k, k)
        p.render(cr)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp"
        surf.write_to_png(tmp)
        os.replace(tmp, dest)
    return doc.get_title() or "", doc.get_author() or "", n
