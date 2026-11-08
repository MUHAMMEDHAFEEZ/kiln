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


# ---- covers
COVER_W, COVER_H = 300, 400
PALETTES = {                                    # per topic, variants of (top left, bottom right, accent), one picked per book
    "ai": [((0.06, 0.09, 0.27), (0.38, 0.19, 0.62), (0.56, 0.82, 1.0)),
           ((0.04, 0.14, 0.30), (0.09, 0.45, 0.62), (0.62, 0.96, 0.95)),
           ((0.19, 0.06, 0.25), (0.62, 0.18, 0.46), (1.0, 0.76, 0.86))],
    "patterns": [((0.04, 0.20, 0.23), (0.09, 0.46, 0.39), (0.99, 0.82, 0.46)),
                 ((0.22, 0.12, 0.04), (0.62, 0.34, 0.10), (1.0, 0.86, 0.55)),
                 ((0.07, 0.17, 0.30), (0.12, 0.40, 0.55), (1.0, 0.80, 0.50))],
    "web": [((0.03, 0.18, 0.13), (0.07, 0.38, 0.26), (0.63, 0.96, 0.72)),
            ((0.05, 0.16, 0.10), (0.20, 0.42, 0.18), (0.82, 0.96, 0.55))],
    "other": [((0.20, 0.09, 0.07), (0.58, 0.27, 0.17), (0.96, 0.72, 0.56)),
              ((0.16, 0.10, 0.20), (0.45, 0.25, 0.45), (0.95, 0.75, 0.90))],
}
TOPIC_LABEL = {"ai": "AI & MACHINE LEARNING", "patterns": "SOFTWARE DESIGN", "web": "WEB & PYTHON", "other": "BOOK"}


def _text(cr, s, x, y, width, size, rgba, bold=False, spacing=0, max_lines=99, align="l"):
    """Wrapped text at the top left (x, y); returns the height used."""
    import gi
    gi.require_version("PangoCairo", "1.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Pango, PangoCairo
    lay = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription()
    fd.set_family("Ubuntu Sans")
    fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    fd.set_absolute_size(size * Pango.SCALE)
    lay.set_font_description(fd)
    lay.set_width(int(width * Pango.SCALE))
    lay.set_wrap(Pango.WrapMode.WORD_CHAR)                 # (a word that is still too long is broken, never cut off)
    lay.set_ellipsize(Pango.EllipsizeMode.END)
    lay.set_height(-max_lines)
    lay.set_alignment({"l": Pango.Alignment.LEFT, "c": Pango.Alignment.CENTER}[align])
    if spacing:
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_letter_spacing_new(int(spacing * Pango.SCALE)))
        lay.set_attributes(attrs)
    lay.set_text(s, -1)
    cr.set_source_rgba(*rgba)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, lay)
    return lay.get_pixel_size()[1], lay.get_line_count()


def _fit_title(cr, s, width, max_h):
    """The biggest size (36 down to 18) at which the title fits in `max_h` pixels."""
    import gi
    gi.require_version("PangoCairo", "1.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Pango, PangoCairo
    for size in range(36, 17, -2):
        lay = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family("Ubuntu Sans")
        fd.set_weight(Pango.Weight.BOLD)
        fd.set_absolute_size(size * Pango.SCALE)
        lay.set_font_description(fd)
        lay.set_width(int(width * Pango.SCALE))
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay.set_text(s, -1)
        widest = 0
        for word in s.split():                              # every word must fit on a line by itself: no broken words
            lay.set_text(word, -1)
            lay.set_width(-1)
            widest = max(widest, lay.get_pixel_size()[0])
            lay.set_width(int(width * Pango.SCALE))
        lay.set_text(s, -1)
        if widest <= width and lay.get_pixel_size()[1] <= max_h:
            return size
    return 18


def _motif(cr, topic, seed, accent, w, h):
    """The picture on a cover, in the lower part: a network (AI), interlocking shapes (patterns), layers (web)."""
    import math
    import random
    rnd = random.Random(seed)
    x0, y0, x1, y1 = 34, h * 0.56, w - 34, h - 62
    cr.set_line_width(1.2)
    if topic == "ai":
        layers = [3, 5, 5, 3] if rnd.random() < 0.5 else [4, 6, 4, 2]
        pts = []
        for i, n in enumerate(layers):
            x = x0 + (x1 - x0) * i / (len(layers) - 1)
            pts.append([(x + rnd.uniform(-5, 5), y0 + (y1 - y0) * (j + 0.5) / n + rnd.uniform(-4, 4)) for j in range(n)])
        for a, b in zip(pts, pts[1:]):
            for p in a:
                for q in b:
                    cr.set_source_rgba(*accent, 0.20)
                    cr.move_to(*p)
                    cr.line_to(*q)
                    cr.stroke()
        for layer in pts:
            for (x, y) in layer:
                cr.set_source_rgba(*accent, 0.9)
                cr.arc(x, y, rnd.choice((3.5, 4.5, 5.5)), 0, 2 * math.pi)
                cr.fill()
    elif topic == "patterns":
        cols, rows = 4, 3
        cw, ch = (x1 - x0) / cols, (y1 - y0) / rows
        for r in range(rows):
            for c in range(cols):
                cx, cy, k = x0 + (c + 0.5) * cw, y0 + (r + 0.5) * ch, min(cw, ch) * 0.36
                kind = rnd.randrange(3)
                cr.set_source_rgba(*accent, 0.85 if (r + c) % 2 == 0 else 0.35)
                if kind == 0:
                    cr.arc(cx, cy, k, 0, 2 * math.pi)
                elif kind == 1:
                    cr.rectangle(cx - k, cy - k, 2 * k, 2 * k)
                else:
                    cr.move_to(cx, cy - k)
                    cr.line_to(cx + k, cy + k)
                    cr.line_to(cx - k, cy + k)
                    cr.close_path()
                if (r + c) % 2 == 0:
                    cr.fill()
                else:
                    cr.set_line_width(1.6)
                    cr.stroke()
    elif topic == "web":
        n, gap = 6, 8
        bh = ((y1 - y0) - gap * (n - 1)) / n
        for i in range(n):
            bw = (x1 - x0) * rnd.uniform(0.45, 1.0)
            cr.set_source_rgba(*accent, 0.20 + 0.65 * (i == n - 2))
            cr.rectangle(x0, y0 + i * (bh + gap), bw, bh)
            cr.fill()
            cr.set_source_rgba(*accent, 0.9)
            cr.arc(x0 + bw + 10, y0 + i * (bh + gap) + bh / 2, 3, 0, 2 * math.pi)
            cr.fill()
    else:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for i in range(1, 6):
            cr.set_source_rgba(*accent, 0.15 + 0.12 * i)
            cr.arc(cx, cy, 16 * i, -math.pi / 2 + i * 0.4, math.pi + i * 0.4)
            cr.stroke()


def design_cover(title, subtitle="", author="", topic="other", seed=0):
    """A cover made for the book: gradient and picture by topic, the title as large as fits, the author at the bottom.
    Returns a 300 x 400 image surface."""
    w, h = COVER_W, COVER_H
    variants = PALETTES.get(topic, PALETTES["other"])
    top, bottom, accent = variants[seed % len(variants)]
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    cr = cairo.Context(surf)
    g = cairo.LinearGradient(0, 0, w, h)
    g.add_color_stop_rgb(0, *top)
    g.add_color_stop_rgb(1, *bottom)
    cr.set_source(g)
    cr.paint()
    _motif(cr, topic, seed, accent, w, h)
    spine = cairo.LinearGradient(0, 0, 14, 0)                      # the shadow where a book's spine would be
    spine.add_color_stop_rgba(0, 0, 0, 0, 0.35)
    spine.add_color_stop_rgba(1, 0, 0, 0, 0)
    cr.set_source(spine)
    cr.rectangle(0, 0, 14, h)
    cr.fill()
    pad, tw = 30, w - 60
    _text(cr, TOPIC_LABEL.get(topic, "BOOK"), pad, 26, tw, 10.5, (*accent, 0.95), bold=True, spacing=1.6)
    cr.set_source_rgba(*accent, 0.8)
    cr.rectangle(pad, 46, 28, 2.5)
    cr.fill()
    max_h = h * 0.56 - 62 - (28 if subtitle else 0)
    size = _fit_title(cr, title, tw, max_h)
    used, _n = _text(cr, title, pad, 60, tw, size, (1, 1, 1, 1), bold=True, max_lines=6)
    if subtitle:
        _text(cr, subtitle, pad, 60 + used + 8, tw, 14, (1, 1, 1, 0.82), max_lines=2)
    if author:
        _text(cr, author, pad, h - 42, tw, 13.5, (1, 1, 1, 0.9), bold=True, max_lines=2)
    cr.set_line_width(1)
    cr.set_source_rgba(1, 1, 1, 0.14)
    cr.rectangle(0.5, 0.5, w - 1, h - 1)
    cr.stroke()
    return surf


def is_blank(surface, limit=0.001):
    """True when a rendered page has (almost) nothing on it: less than 0.1% of it is not white."""
    surface.flush()
    data, stride = surface.get_data(), surface.get_stride()
    w, h = surface.get_width(), surface.get_height()
    dark = total = 0
    for y in range(0, h, 2):
        row = y * stride
        for x in range(0, w, 2):
            o = row + x * 4
            total += 1
            if min(data[o], data[o + 1], data[o + 2]) < 235:
                dark += 1
    return total == 0 or dark / total < limit


def job_cover(path, dest, width=300, mode="auto", fallback_title=""):
    """Title, author and page count of a book, and its cover saved as a PNG for the library. `mode`: auto (the first page
    of the book; a cover drawn by Kiln only when that page is blank), pdf (always the first page) or designed (a drawn
    cover for every book)."""
    doc = Poppler.Document.new_from_file(GLib.filename_to_uri(path, None), None)
    n = doc.get_n_pages()
    title, author = doc.get_title() or "", doc.get_author() or ""
    if n and dest:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        surf = None
        if mode != "designed":
            p = doc.get_page(0)
            pw, ph = p.get_size()
            k = width / pw
            surf = cairo.ImageSurface(cairo.FORMAT_RGB24, width, max(1, round(ph * k)))
            cr = cairo.Context(surf)
            cr.set_source_rgb(1, 1, 1)
            cr.paint()
            cr.scale(k, k)
            p.render(cr)
            if mode == "auto" and is_blank(surf):
                surf = None
        if surf is None:
            import books as BK
            shown = title if len(title.strip()) > 3 and not any(j in title.lower() for j in ("microsoft word", "untitled", ".doc", ".tex")) \
                else fallback_title or title
            main, sub, who = BK.split_title(shown, author)
            surf = design_cover(main, sub, who, BK.cover_topic(shown), seed=sum(map(ord, shown)))
        tmp = dest + ".tmp"
        surf.write_to_png(tmp)
        os.replace(tmp, dest)
    return title, author, n
