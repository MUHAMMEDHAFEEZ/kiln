"""Icon kit for the Violet Desktop icon grid: macOS-style folders + document pages,
generated as SVG on the fly (so colours and glyphs are changeable at runtime)."""
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

PALETTE = [
    ("Blue", "#2f8cff"), ("Purple", "#8b5cf6"), ("Pink", "#ff5fa2"),
    ("Red", "#ff4d4f"), ("Orange", "#ff8a2a"), ("Yellow", "#ffc233"),
    ("Green", "#30c26b"), ("Teal", "#2bc8c0"), ("Graphite", "#6b7280"),
]
DEFAULT_COLOR = "#2f8cff"

# Lucide-style 24x24 outline glyphs (stroke), or ("fill", svg) for solid ones
GLYPHS = {
    "play":     ("fill", '<path d="M7 4.5 19.5 12 7 19.5Z"/>'),
    "dots":     ("fill", '<circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/>'),
    "home":     ("stroke", '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>'),
    "star":     ("stroke", '<path d="m12 2 3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"/>'),
    "heart":    ("stroke", '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>'),
    "code":     ("stroke", '<path d="m16 18 6-6-6-6"/><path d="m8 6-6 6 6 6"/>'),
    "terminal": ("stroke", '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>'),
    "image":    ("stroke", '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21"/>'),
    "music":    ("stroke", '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>'),
    "game":     ("stroke", '<path d="M6 11h4M8 9v4M15 12h.01M18 10h.01"/><path d="M17.32 5H6.68a4 4 0 0 0-3.98 3.59C2.6 9.42 2 14.46 2 16a3 3 0 0 0 3 3c1 0 1.5-.5 2-1l1.41-1.41A2 2 0 0 1 9.83 16h4.34a2 2 0 0 1 1.42.59L17 18c.5.5 1 1 2 1a3 3 0 0 0 3-3c0-1.55-.6-6.58-.68-7.26A4 4 0 0 0 17.32 5z"/>'),
    "file":     ("stroke", '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8M16 13H8M16 17H8"/>'),
    "bolt":     ("stroke", '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>'),
    "globe":    ("stroke", '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'),
    "tools":    ("stroke", '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>'),
    "box":      ("stroke", '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>'),
    "camera":   ("stroke", '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>'),
    "leaf":     ("stroke", '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/><path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/>'),
    "cloud":    ("stroke", '<path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/>'),
}
GLYPH_NAMES = ["none"] + list(GLYPHS)


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    ra, rb = _hex(a), _hex(b)
    return "#%02x%02x%02x" % tuple(int(ra[i] * (1 - t) + rb[i] * t) for i in range(3))


def glyph_svg(name):
    g = GLYPHS.get(name)
    if not g:
        return ""
    kind, body = g
    style = ('fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
             if kind == "stroke" else 'fill="{c}" stroke="{c}" stroke-width="1.6" stroke-linejoin="round"')
    def layer(c, op, dy):
        return (f'<g transform="translate(98 {116 + dy}) scale(2.5)" opacity="{op}" '
                + style.format(c=c) + f'>{body}</g>')
    return layer("#000000", 0.22, 2.5) + layer("#ffffff", 0.92, 0)


def folder_svg(color=DEFAULT_COLOR, glyph="none"):
    light, dark = mix(color, "#ffffff", 0.32), mix(color, "#000000", 0.18)
    back_t, back_b = mix(color, "#000000", 0.26), mix(color, "#000000", 0.36)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
<defs>
 <linearGradient id="bk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{back_t}"/><stop offset="1" stop-color="{back_b}"/></linearGradient>
 <linearGradient id="fr" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{light}"/><stop offset=".5" stop-color="{color}"/><stop offset="1" stop-color="{dark}"/></linearGradient>
 <linearGradient id="hl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".42"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
 <filter id="sh" x="-20%" y="-30%" width="140%" height="180%"><feGaussianBlur stdDeviation="7"/></filter>
</defs>
<ellipse cx="128" cy="226" rx="86" ry="11" fill="#000" fill-opacity=".38" filter="url(#sh)"/>
<path d="M24 66a16 16 0 0 1 16-16h48a16 16 0 0 1 11.3 4.7L114 69h102a16 16 0 0 1 16 16v110a16 16 0 0 1-16 16H40a16 16 0 0 1-16-16z" fill="url(#bk)"/>
<rect x="24" y="86" width="208" height="124" rx="16" fill="url(#fr)"/>
<rect x="24" y="86" width="208" height="52" rx="16" fill="url(#hl)"/>
<rect x="24.5" y="86.5" width="207" height="123" rx="15.5" fill="none" stroke="#fff" stroke-opacity=".30"/>
{glyph_svg(glyph)}
</svg>'''


DOC_COLORS = {
    "pdf": "#ff4d4f", "doc": "#2f8cff", "docx": "#2f8cff", "odt": "#2f8cff", "txt": "#6b7280", "md": "#6b7280",
    "xls": "#30c26b", "xlsx": "#30c26b", "csv": "#30c26b", "ppt": "#ff8a2a", "pptx": "#ff8a2a",
    "zip": "#ffc233", "tar": "#ffc233", "gz": "#ffc233", "7z": "#ffc233", "rar": "#ffc233", "xz": "#ffc233",
    "py": "#8b5cf6", "js": "#ffc233", "ts": "#2f8cff", "html": "#ff8a2a", "css": "#2bc8c0", "json": "#2bc8c0",
    "sh": "#30c26b", "sql": "#ff5fa2", "yml": "#2bc8c0", "yaml": "#2bc8c0",
    "mp3": "#ff5fa2", "wav": "#ff5fa2", "flac": "#ff5fa2", "mp4": "#8b5cf6", "mkv": "#8b5cf6", "mov": "#8b5cf6",
    "deb": "#ff4d4f", "iso": "#6b7280", "chm": "#6b7280", "ttf": "#6b7280", "otf": "#6b7280",
}


def doc_svg(ext):
    ext = (ext or "file").lower()[:4]
    color = DOC_COLORS.get(ext, "#6b7280")
    size = 30 if len(ext) <= 3 else 25
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
<defs>
 <linearGradient id="pg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#e6e6ee"/></linearGradient>
 <linearGradient id="bd" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{mix(color, '#ffffff', .22)}"/><stop offset="1" stop-color="{mix(color, '#000000', .12)}"/></linearGradient>
 <filter id="sh" x="-20%" y="-30%" width="140%" height="180%"><feGaussianBlur stdDeviation="7"/></filter>
</defs>
<ellipse cx="128" cy="232" rx="68" ry="10" fill="#000" fill-opacity=".35" filter="url(#sh)"/>
<path d="M62 24h94l46 46v150a12 12 0 0 1-12 12H62a12 12 0 0 1-12-12V36a12 12 0 0 1 12-12z" fill="url(#pg)"/>
<path d="M156 24v36a12 12 0 0 0 12 12h34z" fill="#cfcfda"/>
<g stroke="#c4c4d0" stroke-width="7" stroke-linecap="round"><path d="M76 100h58M76 122h100M76 144h100"/></g>
<rect x="66" y="168" width="124" height="46" rx="11" fill="url(#bd)"/>
<text x="128" y="201" font-family="Ubuntu Sans, sans-serif" font-weight="700" font-size="{size}" fill="#fff" text-anchor="middle">{ext.upper()}</text>
</svg>'''


def render(svg, px):
    """Render SVG text to a GdkPixbuf at px x px."""
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(px, px)
    loader.write(svg.encode("utf-8"))
    loader.close()
    return loader.get_pixbuf()


def folder_matte_svg(color=DEFAULT_COLOR, glyph="none"):
    """Flat, paper-like folder: no gloss, darker tab, soft contact shadow."""
    back = mix(color, "#000000", 0.30)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
<defs><filter id="sh" x="-20%" y="-30%" width="140%" height="180%"><feGaussianBlur stdDeviation="6"/></filter></defs>
<path d="M28 70a14 14 0 0 1 14-14h44a14 14 0 0 1 10.5 4.5L110 74h104a14 14 0 0 1 14 14v106a14 14 0 0 1-14 14H42a14 14 0 0 1-14-14z" fill="{back}"/>
<rect x="28" y="90" width="200" height="118" rx="14" fill="{color}"/>
<path d="M42 91h172" stroke="#fff" stroke-opacity=".16" stroke-width="2" stroke-linecap="round"/>
{glyph_svg(glyph)}
</svg>'''
