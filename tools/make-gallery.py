#!/usr/bin/python3
"""Rebuild docs/screenshots/themes.jpg from the generated wallpapers in share/wallpapers."""
import json
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = json.load(open(f"{ROOT}/share/themes.json", encoding="utf-8"))
names = data["order"]
W, H, G, COLS = 640, 360, 18, 4
rows = (len(names) + COLS - 1) // COLS
sheet = Image.new("RGB", (COLS * W + (COLS + 1) * G, rows * (H + 44) + G), (24, 23, 21))
draw = ImageDraw.Draw(sheet)
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/ubuntu/Ubuntu[wdth,wght].ttf", 24)
except OSError:
    font = ImageFont.load_default()
for i, n in enumerate(names):
    img = Image.open(f"{ROOT}/share/wallpapers/{n}.jpg").resize((W, H), Image.LANCZOS)
    x, y = G + (i % COLS) * (W + G), G + (i // COLS) * (H + 44)
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, W, H), 18, fill=255)
    sheet.paste(img, (x, y), mask)
    draw.text((x + 4, y + H + 8), data["themes"][n]["label"], fill=(236, 230, 218), font=font)
sheet.save(f"{ROOT}/docs/screenshots/themes.jpg", quality=88, optimize=True)
print("wrote docs/screenshots/themes.jpg")
