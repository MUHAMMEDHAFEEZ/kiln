#!/usr/bin/python3
"""Kiln theme engine: switch the whole look in one go (normally run as `kiln theme`).

    theme.py                list themes (current one marked)
    theme.py <name>         apply a theme
    theme.py --wallpapers   (re)generate the built-in wallpapers

Applies: desktop cards/terminal/icons (restarts the widgets), top bar + its menus and the
lock screen (kiln-shell extension stylesheet), dock colours, GNOME Terminal profile,
GTK/icon theme + light/dark preference, folder colours and the wallpaper. Each system part
can be switched off under "theme_apply" in ~/.config/kiln/config.json.

Wallpapers: put your own at ~/.config/kiln/wallpapers/<theme>.(png|jpg|jpeg|webp);
otherwise the theme's generated wallpaper is used.
"""
import json
import math
import os
import random
import subprocess
import sys

HOME = os.path.expanduser("~")
SHARE = os.path.dirname(os.path.abspath(__file__))
APP = os.environ.get("KILN_CONFIG", f"{HOME}/.config/kiln")
CACHE = os.environ.get("KILN_CACHE", f"{HOME}/.cache/kiln")
THEMES = f"{SHARE}/themes.json"
USER_THEMES = f"{APP}/themes.json"
CURRENT = f"{APP}/current-theme"
STYLES = f"{APP}/icon-styles.json"
UUID = "kiln-shell@local"
EXT = f"{HOME}/.local/share/gnome-shell/extensions/{UUID}"
WALL_DIR = f"{APP}/wallpapers"              # the user's own wallpapers win
GEN_DIR = f"{SHARE}/wallpapers"             # generated at install time (or on first use)


def load_config():
    try:
        with open(f"{APP}/config.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_themes():
    with open(THEMES, encoding="utf-8") as f:
        data = json.load(f)
    try:
        with open(USER_THEMES, encoding="utf-8") as f:
            extra = json.load(f)
        data["themes"].update(extra.get("themes", {}))
        data["order"] += [n for n in extra.get("order", list(extra.get("themes", {}))) if n not in data["order"]]
        data["folder_palettes"].update(extra.get("folder_palettes", {}))
        WALL_SPECS.update(extra.get("wallpapers", {}))
    except (OSError, ValueError):
        pass
    return data


def sh(*cmd):
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def gset(schema, key, value):
    sh("gsettings", "set", schema, key, value)


def rgba(hexs, a):
    r, g, b = (int(hexs[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {a})"


def rgb_tuple(hexs):
    return tuple(int(hexs[i:i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------- wallpapers
WALL_SPECS = {
    # every theme gets a paper-cut mountain range in its own colours (layers: back -> front)
    "kiln-dark": dict(kind="ridges", sky=("1c1b19", "24221f"),
                      layers=["d97757", "c9694b", "4a4541", "383430", "2b2825", "1f1d1b"]),
    "paper-light": dict(kind="ridges", sky=("f3efe6", "ebe4d6"),
                        layers=["e0d6c4", "cdbfa6", "b5a589", "9fb08f", "7f9a78", "5f7d63"]),
    "sepia": dict(kind="ridges", sky=("efe5d2", "e6d8bd"),
                  layers=["dcc9a6", "c8ad80", "b0905f", "927348", "755a37", "5c4529"]),
    "slate-ink": dict(kind="ridges", sky=("111824", "1a2436"),
                      layers=["6f8fbf", "4f6b97", "2f405c", "26344b", "1d283b", "141d2c"]),
    "nordic-frost": dict(kind="ridges", sky=("2b313c", "3b4252"),
                         layers=["88c0d0", "6f9fb3", "4c566a", "434c5e", "3b4252", "2e3440"]),
    "graphite-moss": dict(kind="ridges", sky=("0c0e0d", "141816"),
                          layers=["6b8f5a", "557447", "2c302e", "232725", "1b1e1d", "131514"]),
    "graphite-lime": dict(kind="ridges", sky=("121214", "19191c"),
                          layers=["a8cf2c", "86a624", "2c2c31", "232327", "1b1b1e", "131315"]),
    "violet-dusk": dict(kind="ridges", sky=("0b1030", "151848"),
                        layers=["9b7cf0", "7a5fd6", "2b2470", "221d5c", "1a1649", "110e33"]),
}


def smooth_noise(rng, n=6):
    waves = [(rng.uniform(0.6, 3.2), rng.uniform(0, 6.28), rng.uniform(0.3, 1.0)) for _ in range(n)]
    tot = sum(w[2] for w in waves)
    return lambda t: sum(a * math.sin(f * t * 2 * math.pi + ph) for f, ph, a in waves) / tot


def ridge_profile(rng, W, base, height, peaks, jag):
    """Mountain silhouette: a few dominant peaks (soft tops) plus small jagged detail."""
    ps = [(rng.uniform(-0.1, 1.1) * W, rng.uniform(0.45, 1.0) * height, rng.uniform(0.55, 1.1)) for _ in range(peaks)]
    waves = [(rng.uniform(4, 14), rng.uniform(0, 6.28), rng.uniform(0.2, 1.0)) for _ in range(5)]
    tot = sum(w[2] for w in waves)
    pts = []
    for x in range(0, W + 6, 6):
        y = 0.0
        for px, ph, slope in ps:
            d = abs(x - px) / (W * 0.18 * slope)
            y = max(y, ph * max(0.0, 1 - d ** 1.15))
        det = sum(a * math.sin(f * x / W * 2 * math.pi + o) for f, o, a in waves) / tot
        floor = height * (0.16 + 0.07 * math.sin(x / W * 2 * math.pi * 1.3 + ps[0][0]))   # rolling valleys
        y = max(y, floor) + 0.12 * min(y, floor)
        pts.append((x, base - y - det * jag))
    return pts

def gen_wallpaper(name, spec, path, W=1920, H=1080):
    from PIL import Image, ImageDraw, ImageFilter, ImageChops
    rng = random.Random(name)
    top, bot = rgb_tuple(spec["sky"][0]), rgb_tuple(spec["sky"][1])
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3)))
    kind = spec["kind"]

    if kind == "ridges":                         # layered paper-cut mountain range
        light_sky = sum(top) > 450                # softer shadows on light paper
        shade = 0.12 if light_sky else 0.35
        for i, col in enumerate(spec["layers"]):
            base, height = H * (0.62 + 0.075 * i), H * (0.30 - 0.035 * i)
            pts = [(0, H)] + ridge_profile(rng, W, base, height, peaks=3 + i,
                                           jag=H * (0.008 + 0.004 * i)) + [(W, H)]
            mask = Image.new("L", (W, H), 0)
            ImageDraw.Draw(mask).polygon(pts, fill=255)
            shadow = ImageChops.offset(mask.filter(ImageFilter.GaussianBlur(14)), 0, 9).point(lambda v: int(v * shade))
            img.paste((0, 0, 0), (0, 0), shadow)
            img.paste(rgb_tuple(col), (0, 0), mask)
            edge = Image.new("L", (W, H), 0)
            ImageDraw.Draw(edge).line(pts[1:-1], fill=55, width=2)
            img.paste((255, 255, 255), (0, 0), edge.filter(ImageFilter.GaussianBlur(1.2)))
        grain = Image.effect_noise((W, H), 26).convert("L").point(lambda v: int((v - 128) * 0.14 + 128))
        img = ImageChops.overlay(img, Image.merge("RGB", (grain, grain, grain)))

    if kind in ("ink", "space", "geo"):          # stars / speckles for dark skies
        st = ImageDraw.Draw(img, "RGBA")
        for _ in range(260 if kind != "geo" else 0):
            x, y = rng.uniform(W * 0.35, W), rng.uniform(0, H * 0.55)
            r = rng.choice([0.6, 0.8, 1.0, 1.4])
            st.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, rng.randint(60, 200)))

    if kind == "space":
        neb = Image.new("RGB", (W, H), (0, 0, 0))
        nd = ImageDraw.Draw(neb)
        for i in range(14):
            col = rgb_tuple(rng.choice(spec["blobs"]))
            cx, cy = rng.uniform(W * 0.45, W * 0.95), rng.uniform(H * 0.1, H * 0.8)
            rx, ry = rng.uniform(80, 320), rng.uniform(60, 220)
            nd.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=col)
        neb = neb.filter(ImageFilter.GaussianBlur(90))
        img = ImageChops.add(img, neb.point(lambda v: int(v * 0.55)))

    if kind == "geo":
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(lay)
        for _ in range(9):
            pts = [(rng.uniform(W * 0.3, W * 1.2), rng.uniform(-H * 0.2, H * 1.2)) for _ in range(3)]
            gd.polygon(pts, fill=(255, 255, 255, rng.randint(6, 16)))
        lc = rgb_tuple(spec["line"])
        gd.line([(W * 0.55, H), (W * 1.0, H * 0.35)], fill=lc + (90,), width=2)
        img = Image.alpha_composite(img.convert("RGBA"), lay).convert("RGB")

    if kind in ("paper", "ink"):
        if "sun" in spec:
            sc, sa = spec["sun"]
            sun = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sd = ImageDraw.Draw(sun)
            cx, cy, r = W * 0.76, H * 0.40, 105
            sd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=rgb_tuple(sc) + (int(255 * sa),))
            glow = sun.filter(ImageFilter.GaussianBlur(40))
            img = Image.alpha_composite(img.convert("RGBA"), glow)
            img = Image.alpha_composite(img, sun).convert("RGB")
        for i, col in enumerate(spec["layers"]):
            f = smooth_noise(rng)
            base = H * (0.50 + 0.09 * i)
            amp = H * (0.16 - 0.018 * i)
            pts = [(0, H)]
            for xi in range(0, W + 8, 8):
                left_calm = 0.35 + 0.65 * (xi / W)
                pts.append((xi, base - amp * left_calm * (0.55 + 0.45 * f(xi / W))))
            pts.append((W, H))
            mask = Image.new("L", (W, H), 0)
            ImageDraw.Draw(mask).polygon(pts, fill=255)
            if kind == "paper":                       # paper-cut depth: soft shadow under each sheet
                shadow = mask.filter(ImageFilter.GaussianBlur(10))
                shadow = ImageChops.offset(shadow, 0, 7).point(lambda v: int(v * 0.30))
                img.paste((0, 0, 0), (0, 0), shadow)
            img.paste(rgb_tuple(col), (0, 0), mask)
            if kind == "paper":                       # faint lit edge on top of each sheet
                edge = Image.new("L", (W, H), 0)
                ImageDraw.Draw(edge).line(pts[1:-1], fill=70, width=2)
                img.paste((255, 255, 255), (0, 0), edge.filter(ImageFilter.GaussianBlur(1)))
        if kind == "paper":                           # paper grain
            grain = Image.effect_noise((W, H), 24).convert("L").point(lambda v: int((v - 128) * 0.12 + 128))
            img = ImageChops.overlay(img, Image.merge("RGB", (grain, grain, grain)))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, quality=92, subsampling=0, optimize=True)


def wallpaper_for(name, theme):
    for ext in ("png", "jpg", "jpeg", "webp"):
        p = f"{WALL_DIR}/{name}.{ext}"
        if os.path.exists(p):
            return p
    spec = WALL_SPECS.get(name)
    if not spec:
        return None
    p = f"{GEN_DIR}/{name}.jpg"
    if not os.path.exists(p):
        try:
            gen_wallpaper(name, spec, p)
        except OSError:                          # read-only share dir: fall back to the cache
            p = f"{CACHE}/wallpapers/{name}.jpg"
            if not os.path.exists(p):
                gen_wallpaper(name, spec, p)
    return p


# --------------------------------------------------------------- top bar stylesheet
PANEL_CSS = """/* Kiln shell stylesheet, generated by `kiln theme` ({label}): edit themes.json, not this file */
#panel {{
  background-color: {panel_bg};
  border-bottom: 1px solid {panel_border};
  height: 40px;
  font-weight: 500;
}}
#panel .panel-button {{ color: {panel_fg}; border-radius: 10px; margin: 4px 1px; }}
#panel .panel-button:hover, #panel .panel-button:focus,
#panel .panel-button:active, #panel .panel-button:checked {{ background-color: {panel_hover}; color: {panel_fg}; }}
#panel .kiln-date .clock {{ color: {panel_fg}; padding: 0 6px; }}

.kiln-brand {{ padding: 0 10px; margin: 4px 2px; border-radius: 10px; }}
.kiln-brand:hover, .kiln-brand:focus {{ background-color: {panel_hover}; }}
.kiln-brand-box {{ spacing: 8px; }}
.kiln-logo {{ icon-size: 22px; }}
.kiln-brand-label {{ color: {panel_fg}; font-size: 14px; font-weight: 600; }}
.kiln-ws {{
  background-color: {acc}; color: {on_acc}; border-radius: 7px;
  min-width: 22px; height: 22px; padding: 0 5px; font-size: 13px; font-weight: 700;
}}
.kiln-chevron {{ icon-size: 14px; color: {panel_dim}; }}

.kiln-pill {{
  min-width: 340px; height: 28px; margin: 6px 0; padding: 0 12px; border-radius: 14px;
  background-color: {panel_ink06}; border: 1px solid {panel_line};
}}
.kiln-pill:hover, .kiln-pill:focus {{ background-color: {panel_ink10}; border-color: {acc}; }}
.kiln-pill-box {{ spacing: 10px; }}
.kiln-pill-icon {{ icon-size: 14px; color: {panel_dim}; }}
.kiln-pill-label {{ color: {panel_dim}; font-size: 13px; }}
.kiln-pill-hint {{ color: {panel_dim}; font-size: 11.5px; padding: 1px 7px; border-radius: 6px; background-color: {panel_ink06}; }}

/* menus & pop-ups */
/* the boxpointer stays invisible: the content draws background, border and corners (one radius) */
.popup-menu-boxpointer, .candidate-popup-boxpointer {{
  -arrow-background-color: transparent; -arrow-border-width: 0; -arrow-border-color: transparent;
}}
.popup-menu-content, .candidate-popup-content {{
  background-color: {menu_bg}; border: 1px solid {menu_border}; border-radius: 20px; color: {menu_fg};
}}
.popup-menu-item {{ color: {menu_fg}; }}
.popup-menu-item:hover, .popup-menu-item:focus, .popup-menu-item:selected {{ background-color: {menu_hover}; color: {menu_fg}; }}
.popup-separator-menu-item .popup-separator-menu-item-separator {{ background-color: {menu_line}; }}

/* quick settings */
.quick-settings {{ background-color: {menu_bg}; border: 1px solid {menu_border}; border-radius: 20px; color: {menu_fg}; }}
.quick-toggle {{ background-color: {menu_ink08}; color: {menu_fg}; }}
.quick-toggle:hover {{ background-color: {menu_ink12}; }}
.quick-toggle:checked, .quick-menu-toggle .quick-toggle:checked,
.quick-menu-toggle .quick-toggle-arrow:checked {{ background-color: {sel}; color: {on_sel}; }}
.quick-toggle:checked:hover, .quick-menu-toggle .quick-toggle-arrow:checked:hover {{ background-color: {sel_hover}; }}
.quick-toggle-menu {{ background-color: {menu_bg2}; border-radius: 18px; color: {menu_fg}; }}
.icon-button {{ color: {menu_fg}; }}
.slider {{
  -barlevel-active-background-color: {acc};
  -barlevel-background-color: {menu_ink12};
  -slider-handle-border-color: {acc};
}}

/* calendar & notifications */
.calendar-day.calendar-today, .calendar-today {{ background-color: {sel}; color: {on_sel}; }}
.calendar-day:selected {{ background-color: {menu_hover}; }}
.message, .events-button, .world-clocks-button, .weather-button {{ background-color: {menu_ink06}; border-radius: 14px; }}
.message:hover, .events-button:hover, .world-clocks-button:hover, .weather-button:hover {{ background-color: {menu_ink10}; }}
/* banners are also `.message`, whose translucent hover/focus fill must not show through */
.notification-banner, .notification-banner:hover, .notification-banner:focus, .notification-banner:active {{
  background-color: {menu_solid}; border: 1px solid {menu_border}; border-radius: 16px; color: {menu_fg};
  box-shadow: 0 6px 18px rgba(0,0,0,0.35);
}}
.notification-banner:hover {{ border-color: {acc}; }}
.notification-banner .message-title, .notification-banner .message-source-title {{ color: {menu_fg}; }}
.notification-banner .message-body {{ color: {menu_fg}; }}

/* lock screen */
.unlock-dialog-clock-time {{ color: {text}; font-weight: 300; text-shadow: 0 2px 12px rgba(0,0,0,0.45); }}
.unlock-dialog-clock-date {{ color: {text}; font-weight: 600; text-shadow: 0 1px 8px rgba(0,0,0,0.55); }}
.unlock-dialog-clock-hint {{ color: {panel_dim}; }}
.kiln-lock {{ spacing: 14px; margin-top: 24px; }}
.kiln-lock-card {{ spacing: 10px; }}
.kiln-lock-chip {{
  spacing: 8px; padding: 8px 14px; border-radius: 14px;
  background-color: {lock_card}; border: 1px solid {menu_border};
}}
.kiln-lock-icon {{ icon-size: 16px; color: {acc}; }}
.kiln-lock-label {{ color: {text}; font-size: 14px; }}
.kiln-lock-tiles {{ spacing: 12px; }}
.kiln-lock-tile {{
  spacing: 3px; padding: 12px 18px; border-radius: 18px; min-width: 150px;
  background-color: {lock_card}; border: 1px solid {menu_border};
}}
.kiln-lock-media {{ spacing: 14px; }}
.kiln-lock-art {{
  width: 72px; height: 72px; border-radius: 12px;
  background-color: {menu_ink06}; background-size: cover; background-position: center;
}}
.kiln-lock-art-icon {{ icon-size: 28px; color: {acc}; }}
.kiln-lock-head {{ color: {panel_dim}; font-size: 10.5px; font-weight: 700; letter-spacing: 1px; }}
.kiln-lock-title {{ color: {text}; font-size: 14px; font-weight: 600; max-width: 210px; }}
.kiln-lock-sub {{ color: {panel_dim}; font-size: 12.5px; max-width: 210px; }}
.kiln-lock-prow {{ spacing: 18px; }}
.kiln-lock-pday {{ spacing: 6px; }}
.kiln-lock-pcol {{ spacing: 2px; padding: 6px 9px; border-radius: 10px; min-width: 44px; }}
.kiln-lock-pcol:next {{ background-color: {menu_hover}; }}
.kiln-lock-pname {{ color: {panel_dim}; font-size: 12px; }}
.kiln-lock-ptime {{ color: {text}; font-size: 13px; font-weight: 600; font-feature-settings: "tnum"; }}
.kiln-lock-pcol:next .kiln-lock-pname {{ color: {text}; }}
.kiln-lock-big {{ color: {text}; font-size: 26px; font-weight: 300; font-feature-settings: "tnum"; }}
.kiln-lock-controls {{ spacing: 6px; margin-top: 6px; }}
.kiln-lock-btn {{ border-radius: 99px; padding: 7px; background-color: {menu_ink06}; }}
.kiln-lock-btn:hover {{ background-color: {menu_hover}; }}
.kiln-lock-btn:active {{ background-color: {acc}; }}
.kiln-lock-btn-icon {{ icon-size: 14px; color: {text}; }}
.kiln-lock-bar {{ width: 150px; height: 4px; border-radius: 2px; background-color: {menu_ink10}; margin-top: 8px; }}
.kiln-lock-bar-row {{ width: 110px; margin-top: 0; }}
.kiln-lock-fill {{ border-radius: 2px; background-color: {acc}; }}
.kiln-lock-progress {{
  spacing: 22px; padding: 9px 18px; border-radius: 14px;
  background-color: {lock_card}; border: 1px solid {menu_border};
}}
.kiln-lock-seg {{ spacing: 8px; }}
.kiln-lock-seg-label {{ color: {panel_dim}; font-size: 12.5px; }}
.kiln-lock-seg-value {{ color: {text}; font-size: 12.5px; font-weight: 600; }}
.login-dialog-prompt-entry {{
  background-color: {lock_card}; color: {text}; border: 1px solid {menu_border};
  border-radius: 14px; caret-color: {acc}; selection-background-color: {sel}; selected-color: {on_sel};
}}
.login-dialog-prompt-entry:focus {{ border-color: {acc}; }}
.unlock-dialog-notification-source {{ background-color: {lock_card}; border-radius: 14px; }}

/* Alt+Tab, volume / brightness pop-up */
.switcher-list {{ background-color: {menu_bg}; border: 1px solid {menu_border}; border-radius: 22px; }}
.switcher-list .item-box:selected {{ background-color: {menu_hover}; }}
.osd-window {{ background-color: {menu_bg}; border: 1px solid {menu_border}; color: {menu_fg}; }}
.osd-window .level {{ -barlevel-active-background-color: {acc}; }}
"""


LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 32 32">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="{a1}"/><stop offset="1" stop-color="{a2}"/></linearGradient></defs>
  <path d="M16 1.5 30.5 16 16 30.5 1.5 16Z" fill="url(#g)"/>
  <path d="M16 8.5 23.5 16 16 23.5 8.5 16Z" fill="{hole}"/>
  <path d="M16 12.5 19.5 16 16 19.5 12.5 16Z" fill="url(#g)"/>
</svg>
"""


def panel_css(t):
    c, v = t["colors"], t["values"]
    light_menu = t["mode"] == "light"
    ink_menu = "000000" if light_menu else "ffffff"
    panel_light = sum(rgb_tuple(c["panel"])) > 380
    ink_panel = "000000" if panel_light else "ffffff"
    return PANEL_CSS.format(
        label=t.get("label", ""),
        panel_bg=rgba(c["panel"], v.get("panel_a", 0.92)), panel_fg="#" + c["panel_fg"],
        panel_dim=rgba(c["panel_fg"], 0.62), panel_border=rgba(c["border"], 0.45),
        panel_hover=rgba(c["sel"], 0.30), panel_ink06=rgba(ink_panel, 0.06), panel_ink10=rgba(ink_panel, 0.10),
        panel_line=rgba(c["border"], 0.55), acc="#" + c["acc"],
        menu_bg=rgba(c["menu"], 0.97), menu_bg2=rgba(c["menu"], 1.0), menu_fg="#" + c["menu_fg"],
        menu_border=rgba(c["border"], 0.6), menu_hover=rgba(c["sel"], 0.30), menu_line=rgba(ink_menu, 0.10),
        menu_ink06=rgba(ink_menu, 0.06), menu_ink08=rgba(ink_menu, 0.08), menu_ink10=rgba(ink_menu, 0.10),
        menu_ink12=rgba(ink_menu, 0.12), sel="#" + c["sel"], on_sel="#" + c["on_sel"], on_acc="#" + c["on_acc"],
        sel_hover=rgba(c["sel"], 0.85), text="#" + c["text"], lock_card=rgba(c["card"], 0.78), menu_solid=rgba(c["menu"], 1.0))


# --------------------------------------------------------------- apply
def desktop_folders():
    """Folders on the desktop in the order the icon grid shows them (icons.txt, then A-Z)."""
    d = os.path.expanduser(load_config().get("desktop_dir") or "")
    if not d:
        try:
            d = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True).stdout.strip()
        except OSError:
            d = ""
    d = d or f"{HOME}/Desktop"
    try:
        names = [n for n in os.listdir(d) if not n.startswith(".") and os.path.isdir(os.path.join(d, n))]
    except OSError:
        return []
    try:
        order = [l.strip() for l in open(f"{APP}/icons.txt", encoding="utf-8") if l.strip()]
    except OSError:
        order = []
    rank = {n: i for i, n in enumerate(order)}
    return sorted(names, key=lambda n: (rank.get(n, len(rank)), n.lower()))


def apply(name):
    data = load_themes()
    if name not in data["themes"]:
        sys.exit(f"kiln theme: unknown theme '{name}'. Try: {', '.join(data['order'])}")
    t = data["themes"][name]
    c, v, sysd = t["colors"], t["values"], t["system"]
    parts = {"gtk": True, "dock": True, "gnome_terminal": True, "wallpaper": True}
    parts.update(load_config().get("theme_apply", {}))
    os.makedirs(APP, exist_ok=True)
    os.makedirs(CACHE, exist_ok=True)
    with open(CURRENT, "w", encoding="utf-8") as f:
        f.write(name + "\n")

    # GTK / icons / light-dark preference
    if parts["gtk"]:
        gset("org.gnome.desktop.interface", "gtk-theme", sysd["gtk_theme"])
        gset("org.gnome.desktop.interface", "icon-theme", sysd["icon_theme"])
        gset("org.gnome.desktop.interface", "color-scheme", sysd["color_scheme"])

    # wallpaper
    wp = wallpaper_for(name, t) if parts["wallpaper"] else None
    if wp:
        uri = "file://" + wp
        gset("org.gnome.desktop.background", "picture-uri", uri)
        gset("org.gnome.desktop.background", "picture-uri-dark", uri)
        gset("org.gnome.desktop.background", "picture-options", "zoom")

    # dock (Ubuntu Dock / Dash to Dock)
    D = "org.gnome.shell.extensions.dash-to-dock"
    if parts["dock"]:
        gset(D, "custom-background-color", "true")
        gset(D, "background-color", "#" + c["dock"])
        gset(D, "background-opacity", str(v.get("dock_a", 0.8)))
        gset(D, "custom-theme-customize-running-dots", "true")
        gset(D, "custom-theme-running-dots-color", "#" + c["acc"])
        gset(D, "custom-theme-running-dots-border-color", "#" + c["acc"])

    # GNOME Terminal profile
    try:
        if not parts["gnome_terminal"]:
            raise OSError
        prof = subprocess.run(["gsettings", "get", "org.gnome.Terminal.ProfilesList", "default"],
                              capture_output=True, text=True).stdout.strip().strip("'")
        T = f"org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:{prof}/"
        gset(T, "use-theme-colors", "false")
        gset(T, "background-color", "#" + sysd["term_bg"])
        gset(T, "foreground-color", "#" + sysd["term_fg"])
        gset(T, "cursor-colors-set", "true")
        gset(T, "cursor-background-color", "#" + sysd["term_cursor"])
        gset(T, "cursor-foreground-color", "#" + sysd["term_bg"])
        gset(T, "palette", str(sysd["term_palette"]))
        gset(T, "use-transparent-background", "false" if t["mode"] == "light" else "true")
        gset(T, "background-transparency-percent", "6")
    except OSError:
        pass

    # prompt colours for the desktop terminal card
    def tc(h):
        return ";".join(str(x) for x in rgb_tuple(h))
    ps1 = (f"\\[\\e[38;2;{tc(c['acc'])}m\\]\\u@\\h\\[\\e[0m\\] \\[\\e[38;2;{tc(c['dim'])}m\\]⋮\\[\\e[0m\\] "
           f"\\[\\e[38;2;{tc(c['blue'])}m\\]\\w\\[\\e[0m\\]\\n\\[\\e[38;2;{tc(c['acc'])}m\\]\\$\\[\\e[0m\\] ")
    with open(f"{CACHE}/term.bashrc", "w", encoding="utf-8") as f:
        f.write("# bashrc for the desktop terminal card (generated by `kiln theme`)\n"
                "[ -f ~/.bashrc ] && . ~/.bashrc\n"
                f"PS1='{ps1}'\n")

    # folder colours: the palette's colours go round the desktop folders in grid order
    # (glyphs a folder was given are kept)
    pal = data["folder_palettes"].get(sysd.get("folders", "muted"), {})
    try:
        st = json.load(open(STYLES, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    cyc = pal.get("cycle") or []
    if pal.get("home"):
        st.setdefault("@home", {})["color"] = pal["home"]
    for i, n in enumerate(desktop_folders()):
        if cyc:
            st.setdefault(n, {})["color"] = cyc[i % len(cyc)]
    with open(STYLES + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)
    os.replace(STYLES + ".tmp", STYLES)

    # top bar + menus (reload the extension so the stylesheet is re-read)
    if os.path.isdir(EXT):
        with open(f"{EXT}/logo.svg", "w", encoding="utf-8") as f:       # brand logo in the theme accent
            f.write(LOGO_SVG.format(a1="#" + c["acc2"], a2="#" + c["acc"], hole="#" + c["panel"]))
        with open(f"{EXT}/stylesheet.css", "w", encoding="utf-8") as f:
            f.write(panel_css(t))
        sh("gnome-extensions", "disable", UUID)
        sh("gnome-extensions", "enable", UUID)

    # desktop widgets
    if os.environ.get("KILN_NO_RESTART") != "1":
        sh(f"{SHARE}/start.sh")
    print(f"kiln: applied {t.get('label', name)}")


def main():
    data = load_themes()
    cur = open(CURRENT).read().strip() if os.path.exists(CURRENT) else "kiln-dark"
    if len(sys.argv) < 2 or sys.argv[1] in ("list", "-l", "--list"):
        for n in data["order"]:
            print(("* " if n == cur else "  ") + f"{n:15s} {data['themes'][n].get('label', '')}")
        return
    if sys.argv[1] == "--wallpapers":            # (re)generate all placeholder wallpapers
        for n in data["order"]:
            if n in WALL_SPECS:
                p = f"{GEN_DIR}/{n}.jpg"
                if os.path.exists(p):
                    os.remove(p)
                print(n, "->", wallpaper_for(n, data["themes"][n]))
        return
    apply(sys.argv[1])


if __name__ == "__main__":
    main()
