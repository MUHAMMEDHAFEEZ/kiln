#!/usr/bin/python3
"""Snapping and lining up of rectangles, for arranging widgets (no GTK: it works on (x, y, w, h) tuples).

    snap_move(rect, others, screen)      a dragged rectangle sticks to the edges and centres of the others, next to them
                                         (with the same gap Kiln uses between cards), and to the screen
    snap_resize(rect, others, screen)    the right and bottom edge of a rectangle that is being resized
    align(rects, how)                    left | hcenter | right | top | vcenter | bottom
    distribute(rects, axis)              the same space between three or more, along "h" or "v"
    line_up(rects, axis, gap)            one after the other with `gap` between, in a "row" or a "column"

Every function returns new places and leaves its arguments alone. A guide is {"orient": "v" or "h", "pos", "a", "b"}: a line
at x (or y) `pos` from `a` to `b` along the other axis, to be drawn while you drag."""

GAP = 18            # the space between cards in Kiln's own layout
THRESHOLD = 14      # how close (in pixels) an edge has to come before it sticks


def _edges(lo, size):
    return lo, lo + size / 2.0, lo + size


def _axis(lo, size, others, length, gap, margin, thresh):
    """The best move (a number of pixels) along one axis, or None, and the (kind, value) targets that it matched.
    `others` is a list of (start, size). Kinds: 'lo' (our start meets it), 'mid' (centres meet), 'hi' (our end meets it)."""
    m_lo, m_mid, m_hi = _edges(lo, size)
    cands = []                                              # (distance to move, which of our points, the line it lands on)
    for o_lo, o_size in others:
        o0, oc, o1 = _edges(o_lo, o_size)
        for t in (o0, o1 + gap, o1):                        # our start: flush with its start, next to its end (with the gap), touching it
            cands.append((t - m_lo, "lo", t))
        for t in (o1, o0 - gap, o0):                        # our end: flush with its end, before its start (with the gap), touching it
            cands.append((t - m_hi, "hi", t))
        cands.append((oc - m_mid, "mid", oc))
    for t in (margin, ):
        cands.append((t - m_lo, "lo", t))
    for t in (length - margin, ):
        cands.append((t - m_hi, "hi", t))
    cands.append((length / 2.0 - m_mid, "mid", length / 2.0))
    near = [c for c in cands if abs(c[0]) <= thresh]
    if not near:
        return None, []
    best = min(abs(c[0]) for c in near)
    d = next(c[0] for c in near if abs(c[0]) == best)
    return d, [(kind, t) for dist, kind, t in near if abs(dist - d) < 0.5]


def _guides(rect, others, hits, orient):
    """The lines to draw for the matches on one axis: each at the line the edge landed on, long enough to join our rectangle
    and the rectangle it matched."""
    x, y, w, h = rect
    out = []
    for kind, t in hits:
        pos = t
        if orient == "v":
            ys = [y, y + h]
            for ox, oy, ow, oh in others:
                if min(abs(pos - e) for e in _edges(ox, ow)) < 1.5:
                    ys += [oy, oy + oh]
            out.append({"orient": "v", "pos": pos, "a": min(ys), "b": max(ys)})
        else:
            xs = [x, x + w]
            for ox, oy, ow, oh in others:
                if min(abs(pos - e) for e in _edges(oy, oh)) < 1.5:
                    xs += [ox, ox + ow]
            out.append({"orient": "h", "pos": pos, "a": min(xs), "b": max(xs)})
    return out


def snap_move(rect, others, screen, thresh=THRESHOLD, gap=GAP, margin=0):
    """(x, y, guides): where a dragged `rect` lands. `others` are the rectangles that stay; `screen` is (width, height)."""
    x, y, w, h = rect
    dx, hx = _axis(x, w, [(o[0], o[2]) for o in others], screen[0], gap, margin, thresh)
    dy, hy = _axis(y, h, [(o[1], o[3]) for o in others], screen[1], gap, margin, thresh)
    nx, ny = x + (dx or 0), y + (dy or 0)
    placed = (nx, ny, w, h)
    guides = (_guides(placed, others, hx, "v") if dx is not None else []) + (_guides(placed, others, hy, "h") if dy is not None else [])
    return nx, ny, guides


def _hi_axis(lo, size, others, length, gap, thresh):
    """Like _axis but only the far edge moves (a resize): returns the new size, or None."""
    hi = lo + size
    cands = []
    for o_lo, o_size in others:
        o0, _oc, o1 = _edges(o_lo, o_size)
        cands += [(o1 - hi, o1), (o0 - gap - hi, o0 - gap), (o0 - hi, o0)]
    cands.append((length - hi, length))
    near = [c for c in cands if abs(c[0]) <= thresh]
    if not near:
        return None, []
    best = min(near, key=lambda c: abs(c[0]))
    return size + best[0], [("hi", best[1])]


def snap_resize(rect, others, screen, thresh=THRESHOLD, gap=GAP):
    """(w, h, guides): the size a rectangle gets when its right and bottom edge are dragged to (x + w, y + h)."""
    x, y, w, h = rect
    nw, hx = _hi_axis(x, w, [(o[0], o[2]) for o in others], screen[0], gap, thresh)
    nh, hy = _hi_axis(y, h, [(o[1], o[3]) for o in others], screen[1], gap, thresh)
    nw, nh = nw if nw is not None else w, nh if nh is not None else h
    placed = (x, y, nw, nh)
    return nw, nh, _guides(placed, others, hx, "v") + _guides(placed, others, hy, "h")


# ---------------------------------------------------------------- lining up several
def bounds(rects):
    xs0 = [r[0] for r in rects]
    ys0 = [r[1] for r in rects]
    xs1 = [r[0] + r[2] for r in rects]
    ys1 = [r[1] + r[3] for r in rects]
    return min(xs0), min(ys0), max(xs1), max(ys1)


def align(rects, how):
    """{key: (x, y)} with the rectangles lined up on their left, horizontal centre, right, top, vertical centre or bottom."""
    if len(rects) < 2:
        return {k: (r[0], r[1]) for k, r in rects.items()}
    x0, y0, x1, y1 = bounds(list(rects.values()))
    out = {}
    for k, (x, y, w, h) in rects.items():
        nx, ny = x, y
        if how == "left":
            nx = x0
        elif how == "right":
            nx = x1 - w
        elif how == "hcenter":
            nx = (x0 + x1) / 2.0 - w / 2.0
        elif how == "top":
            ny = y0
        elif how == "bottom":
            ny = y1 - h
        elif how == "vcenter":
            ny = (y0 + y1) / 2.0 - h / 2.0
        else:
            raise ValueError(f"unknown way to align: {how}")
        out[k] = (int(round(nx)), int(round(ny)))
    return out


def distribute(rects, axis):
    """{key: (x, y)} with the same space between the rectangles along "h" or "v" (the first and the last stay where they are)."""
    if axis not in ("h", "v"):
        raise ValueError("axis is h or v")
    i = 0 if axis == "h" else 1
    order = sorted(rects, key=lambda k: rects[k][i])
    out = {k: (rects[k][0], rects[k][1]) for k in rects}
    if len(order) < 3:
        return out
    first, last = rects[order[0]], rects[order[-1]]
    span = (last[i] + last[i + 2]) - first[i]
    space = (span - sum(rects[k][i + 2] for k in order)) / (len(order) - 1)
    pos = first[i]
    for k in order:
        x, y, w, h = rects[k]
        out[k] = (int(round(pos)), y) if axis == "h" else (x, int(round(pos)))
        pos += rects[k][i + 2] + space
    return out


def line_up(rects, axis, gap=GAP):
    """{key: (x, y)}: the rectangles one after the other (left to right for "row", top to bottom for "column") with `gap`
    between them, starting where the first is and on the line of the first."""
    if axis not in ("row", "column"):
        raise ValueError("axis is row or column")
    i = 0 if axis == "row" else 1
    order = sorted(rects, key=lambda k: (rects[k][i], rects[k][1 - i]))
    first = rects[order[0]]
    pos, out = first[i], {}
    for k in order:
        w, h = rects[k][2], rects[k][3]
        out[k] = (int(round(pos)), first[1]) if axis == "row" else (first[0], int(round(pos)))
        pos += (w if axis == "row" else h) + gap
    return out
