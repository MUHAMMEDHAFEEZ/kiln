#!/usr/bin/python3
"""Generate Kiln's brand assets (docs/brand/) from code, so they stay exact and reproducible.

    tools/make-brand.py          writes SVG + PNG files to docs/brand/

Needs: Pillow, GdkPixbuf (python3-gi, for rendering SVG) and fontTools (pip install fonttools; it
turns the wordmark into outlines, so the SVG logos need no font). The wordmark is set in
Ubuntu Bold (Ubuntu Font Licence).
"""
import math
import os
import sys

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = f"{ROOT}/docs/brand"
sys.path.insert(0, f"{ROOT}/share")

# palette
TERRA, TERRA_LIGHT, TERRA_DEEP = "#d97757", "#e8a088", "#b5573a"
BG, CARD, CREAM = "#1c1b19", "#24221f", "#ece6da"

# --- geometry of the mark, in a 1254-unit design space (diamond centre at 627,627)
C = 627.0
R = 385.5                     # half diagonal of the unrounded diamond
T = 85.0                      # how far each corner is rounded off along the edges
VIS = 2 * (R - T * math.sqrt(0.5) / 2)   # visible width of the rounded diamond

# ridge curves: (|dx|, dy) from the centre, dy < 0 is above it (measured from the approved artwork)
TOP_MID = [(0, -160), (60, -146), (100, -123), (140, -97), (180, -70), (220, -47), (260, -28),
           (300, -15), (340, -6), (386, -2)]
MID_BOT = [(0, -1), (60, 14), (100, 36), (140, 60), (180, 83), (220, 100), (260, 112), (300, 121),
           (386, 127)]
SMALL_RIDGE = [(0, -88), (60, -76), (100, -58), (140, -38), (180, -18), (220, -1), (260, 11),
               (300, 19), (386, 24)]   # the single ridge of the 2-layer variant


def _mirror(pts):
    return [(-x, y) for x, y in reversed(pts[1:])] + pts


def _catmull(points):
    p = [points[0]] + points + [points[-1]]
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        yield ((p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6),
               (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6), p2)


def _curve(pts):
    pts = [(C + x, C + y) for x, y in _mirror(pts)]
    d = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"
    for c1, c2, p in _catmull(pts):
        d += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p[0]:.1f},{p[1]:.1f}"
    return d, pts


def ridge_fill(pts):
    d, p = _curve(pts)
    return f"M{p[0][0]:.1f},1100 L{d[1:]} L{p[-1][0]:.1f},1100Z"


def diamond_path():
    k = T * math.sqrt(0.5)
    top, right, bot, left = (C, C - R), (C + R, C), (C, C + R), (C - R, C)
    return (f"M{top[0] - k:.1f},{top[1] + k:.1f} Q{top[0]},{top[1]} {top[0] + k:.1f},{top[1] + k:.1f} "
            f"L{right[0] - k:.1f},{right[1] - k:.1f} Q{right[0]},{right[1]} {right[0] - k:.1f},{right[1] + k:.1f} "
            f"L{bot[0] + k:.1f},{bot[1] - k:.1f} Q{bot[0]},{bot[1]} {bot[0] - k:.1f},{bot[1] - k:.1f} "
            f"L{left[0] + k:.1f},{left[1] + k:.1f} Q{left[0]},{left[1]} {left[0] + k:.1f},{left[1] - k:.1f}Z")


def mark_body(variant="full", mono=None, gap_color=None, uid="k"):
    """SVG elements of the mark in design space. variant: full | small (2 layers).
    mono: one flat colour instead of the terracotta bands (ridges become thin knock-out lines)."""
    clip = f'<clipPath id="{uid}c"><path d="{diamond_path()}"/></clipPath>'
    ridges = [TOP_MID, MID_BOT] if variant == "full" else [SMALL_RIDGE]
    if mono:
        lines = "".join(f'<path d="{_curve(r)[0]}" fill="none" stroke="#000" stroke-width="16"/>' for r in ridges)
        mask = (f'<mask id="{uid}m" maskUnits="userSpaceOnUse" x="0" y="0" width="1254" height="1254">'
                f'<rect width="1254" height="1254" fill="#fff"/>{lines}</mask>')
        return (f'<defs>{clip}{mask}</defs><g clip-path="url(#{uid}c)" mask="url(#{uid}m)">'
                f'<rect x="200" y="200" width="854" height="854" fill="{mono}"/></g>')
    if variant == "full":
        fills = [TERRA_LIGHT, TERRA, TERRA_DEEP]
    else:
        fills = [TERRA_LIGHT, TERRA_DEEP]
    bands = "".join(f'<path d="{ridge_fill(r)}" fill="{fills[i + 1]}"/>' for i, r in enumerate(ridges))
    return (f'<defs>{clip}</defs><g clip-path="url(#{uid}c)"><rect x="200" y="200" width="854" height="854" '
            f'fill="{fills[0]}"/>{bands}</g>')


def svg_symbol(size=1024, pad=0.12, bg=None, radius=0.22, bg_pad=None, **kw):
    """The mark centred in a square canvas. pad = empty space around the visible diamond."""
    s = size * (1 - 2 * pad) / VIS
    t = size / 2 - C * s
    back = f'<rect width="{size}" height="{size}" rx="{size * radius:.1f}" fill="{bg}"/>' if bg else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f'{back}<g transform="translate({t:.2f},{t:.2f}) scale({s:.5f})">{mark_body(**kw)}</g></svg>\n')


# --- wordmark as outlines
def bold_font(weight=700):
    """A static Ubuntu instance cut from the variable font (the Ubuntu-B.ttf file on many systems is not bold)."""
    path = f"{OUT}/.ubuntu-{weight}.ttf"
    if not os.path.exists(path):
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer
        vf = TTFont("/usr/share/fonts/truetype/ubuntu/Ubuntu[wdth,wght].ttf")
        instancer.instantiateVariableFont(vf, {"wght": weight, "wdth": 100}).save(path)
    return path


def wordmark_path(text="Kiln", size=100.0):
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    font = TTFont(bold_font())
    gs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    upm = font["head"].unitsPerEm
    k = size / upm
    x, parts, bounds = 0.0, [], [1e9, 1e9, -1e9, -1e9]
    tracking = 0.012 * upm
    for ch in text:
        g = cmap[ord(ch)]
        pen = SVGPathPen(gs)
        gs[g].draw(TransformPen(pen, (k, 0, 0, -k, x * k, 0)))
        parts.append(pen.getCommands())
        bp = BoundsPen(gs)
        gs[g].draw(TransformPen(bp, (k, 0, 0, -k, x * k, 0)))
        if bp.bounds:
            b = bp.bounds
            bounds = [min(bounds[0], b[0]), min(bounds[1], b[1]), max(bounds[2], b[2]), max(bounds[3], b[3])]
        x += hmtx[g][0] + tracking
    return " ".join(parts), bounds


def svg_lockup(text_color, width=2400, height=800):
    mark_h = height * 0.62                            # visible diamond height
    s = mark_h / VIS
    mx = height * 0.17                                # left margin
    my = height / 2 - C * s
    d, b = wordmark_path("Kiln", 100.0)
    tsize = mark_h * 0.78
    ts = tsize / 100.0
    tx = mx + mark_h + height * 0.10 - b[0] * ts
    ty = height / 2 - (b[1] + b[3]) / 2 * ts
    total = (tx + b[2] * ts) + height * 0.17
    w = int(total)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{height}" viewBox="0 0 {w} {height}">'
            f'<g transform="translate({mx - (C - VIS / 2) * s:.2f},{my:.2f}) scale({s:.5f})">{mark_body(uid="l")}</g>'
            f'<path transform="translate({tx:.2f},{ty:.2f}) scale({ts:.4f})" d="{d}" fill="{text_color}"/></svg>\n')


# --- helpers
def write(name, text):
    with open(f"{OUT}/{name}", "w", encoding="utf-8") as f:
        f.write(text)


def raster(svg_name, png_name, width=None, height=None):
    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(f"{OUT}/{svg_name}", width or -1, height or -1)
    pb.savev(f"{OUT}/{png_name}", "png", [], [])
    return Image.open(f"{OUT}/{png_name}").convert("RGBA")


def font(size):
    return ImageFont.truetype(bold_font(), size)


LAYERS = ["#d97757", "#c9694b", "#4a4541", "#383430", "#2b2825", "#1f1d1b"]


def mountains(w, h, band):
    """Kiln Dark's paper-cut mountains as a w x h picture, confined to the lower `band` of the height."""
    import random
    import theme as T
    from PIL import ImageChops, ImageFilter
    rng = random.Random("kiln-banner")
    img = Image.new("RGB", (w, h), BG)
    for i, col in enumerate(LAYERS):
        base = h * (1 - band * 0.46 + band * 0.105 * i)
        height = h * band * (0.52 - 0.05 * i)
        pts = [(0, h)] + T.ridge_profile(rng, w, base, height, 3 + i, h * 0.006) + [(w, h)]
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).polygon(pts, fill=255)
        shadow = ImageChops.offset(mask.filter(ImageFilter.GaussianBlur(h * 0.012)), 0, int(h * 0.008))
        img.paste((0, 0, 0), (0, 0), shadow.point(lambda v: int(v * 0.35)))
        img.paste(col, (0, 0), mask)
        edge = Image.new("L", (w, h), 0)
        ImageDraw.Draw(edge).line(pts[1:-1], fill=55, width=2)
        img.paste((255, 255, 255), (0, 0), edge.filter(ImageFilter.GaussianBlur(1)))
    grain = Image.effect_noise((w, h), 26).convert("L").point(lambda v: int((v - 128) * 0.14 + 128))
    return ImageChops.overlay(img, Image.merge("RGB", (grain, grain, grain)))


def banner(w, h, mark_px, tag_px, name_px, band):
    img = mountains(w, h, band)
    mark = raster("mark.svg", ".m.png", mark_px, mark_px)
    d = ImageDraw.Draw(img)
    x0 = int(w * 0.07)
    cy = int(h * 0.30)
    img.paste(mark, (x0 - int(mark_px * 0.12), cy - mark_px // 2), mark)
    nx = x0 + int(mark_px * 0.92)
    d.text((nx, cy - int(name_px * 0.64)), "Kiln", font=font(name_px), fill=CREAM)
    d.text((x0, cy + mark_px // 2 + int(tag_px * 0.55)), "A warm, work-focused desktop for GNOME.",
           font=font(tag_px), fill="#bdb6a8")
    os.remove(f"{OUT}/.m.png")
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    # symbols
    write("mark.svg", svg_symbol(1024, 0.12))
    write("mark-2layer.svg", svg_symbol(1024, 0.12, variant="small"))
    write("mark-cream.svg", svg_symbol(1024, 0.12, mono=CREAM))
    write("mark-dark.svg", svg_symbol(1024, 0.12, mono=BG))
    # app icons
    write("app-icon.svg", svg_symbol(1024, 0.22, bg=BG))
    write("app-icon-2layer.svg", svg_symbol(1024, 0.22, bg=BG, variant="small"))
    for n in ("mark", "mark-2layer", "mark-cream", "mark-dark", "app-icon", "app-icon-2layer"):
        raster(f"{n}.svg", f"{n}.png", 1024, 1024)
    # legibility sheet
    sheet = Image.new("RGB", (1500, 640), BG)
    d = ImageDraw.Draw(sheet)
    x = 60
    for label, name, sizes in (("3 layers", "app-icon.svg", (256, 128, 64, 32, 16)),
                               ("2 layers (small sizes)", "app-icon-2layer.svg", (64, 32, 16))):
        d.text((x, 40), label, font=font(30), fill=CREAM)
        xx = x
        for sz in sizes:
            ic = raster(name, ".t.png", sz, sz)
            sheet.paste(ic, (xx, 110 + (256 - sz) // 2), ic)
            d.text((xx, 400), f"{sz}px", font=font(22), fill="#9a948a")
            xx += sz + 50
        x = 920
    os.remove(f"{OUT}/.t.png")
    sheet.save(f"{OUT}/app-icon-sizes.png")
    # lockups
    write("lockup-light.svg", svg_lockup(CREAM))
    write("lockup-dark.svg", svg_lockup(BG))
    raster("lockup-light.svg", "lockup-light.png", None, 800)
    raster("lockup-dark.svg", "lockup-dark.png", None, 800)
    # banners and avatar
    banner(1280, 640, 200, 36, 160, 0.40).save(f"{OUT}/social-preview.png", optimize=True)
    banner(1600, 420, 130, 28, 104, 0.42).save(f"{OUT}/banner.png", optimize=True)
    raster("app-icon.svg", "avatar.png", 800, 800)
    # files the app itself uses
    share = f"{ROOT}/share/desktop"
    os.makedirs(f"{share}/icons", exist_ok=True)
    with open(f"{share}/kiln.svg", "w", encoding="utf-8") as f:
        f.write(svg_symbol(512, 0.16, bg=BG, radius=0.22))
    with open(f"{share}/kiln-small.svg", "w", encoding="utf-8") as f:       # recoloured per theme (theme.py)
        f.write(svg_symbol(512, 0.16, bg=BG, radius=0.22, variant="small"))
    for px in (16, 22, 24, 32):                       # the 2-layer mark is clearer at small sizes
        src = "app-icon-2layer.svg" if px <= 24 else "app-icon.svg"
        pb = GdkPixbuf.Pixbuf.new_from_file_at_size(f"{OUT}/{src}", px, px)
        pb.savev(f"{share}/icons/{px}.png", "png", [], [])
    # top-bar logo template: the theme engine fills {c1} {c2} {c3} with the theme's colours
    tpl = svg_symbol(64, 0.04).replace(TERRA_LIGHT, "{c1}").replace(TERRA, "{c2}").replace(TERRA_DEEP, "{c3}")
    with open(f"{share}/mark-template.svg", "w", encoding="utf-8") as f:
        f.write(tpl)
    for f in os.listdir(OUT):
        if f.startswith(".ubuntu-"):
            os.remove(f"{OUT}/{f}")
    print("wrote", ", ".join(sorted(f for f in os.listdir(OUT))))


if __name__ == "__main__":
    main()
