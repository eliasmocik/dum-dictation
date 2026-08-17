#!/usr/bin/env python3
"""
dum's mark: the word "dum" set heavy and tall inside a circle, black on white.

Lives in src/ (not packaging/) because the RUNNING app needs it: tray.py draws the menu-bar
glyph with monogram(), so the status item and the app icon are the same mark rather than two
that drift apart. packaging/make_icon.py imports this to bake the .icns at build time.

A lazy import from packaging/ would not survive freezing - PyInstaller cannot see an import
hidden behind sys.path manipulation, so the bundled app would silently fall back.

On the reference: a true circle monogram uses purpose-drawn letterforms whose outer edges ARE
the circle. Stretching a normal font that far slices the outer letters' corners off and "dum"
starts reading as "lun" - a circle narrows at top and bottom, so anything spanning the full
diameter loses its extremes. So the word is fitted inside the disc and stretched vertically
for weight and presence, which keeps it legible down to the 16px the menu bar actually uses.
"""
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent

# Heaviest grotesque macOS ships. Weight matters more than shape here: at 16-22px a light
# face turns to mush, and the menu bar is where this is seen most.
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
TEXT = "dum"
FIT = 0.78        # word width as a fraction of the diameter
STRETCH = 1.45    # vertical exaggeration - presence without clipping the letters


def _font_path():
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def monogram(size, text=TEXT, fg=(0, 0, 0, 255), bg=(255, 255, 255, 255),
             fit=FIT, stretch=STRETCH):
    """The mark at `size` px. Drawn at 8x and downsampled so edges stay clean when macOS
    renders it at 16px."""
    S = max(8, size * 8)
    path = _font_path()
    f = ImageFont.truetype(path, 400) if path else ImageFont.load_default()

    ink = Image.new("L", (5000, 1600), 0)
    ImageDraw.Draw(ink).text((100, 100), text, font=f, fill=255)
    g = ink.crop(ink.getbbox())
    w = max(1, int(S * fit))
    h = max(1, int(g.size[1] * (w / g.size[0]) * stretch))
    g = g.resize((w, h), Image.LANCZOS)

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((0, 0, S - 1, S - 1), fill=bg)
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    layer.paste(Image.new("RGBA", g.size, fg),
                ((S - g.size[0]) // 2, (S - g.size[1]) // 2), g)
    img = Image.alpha_composite(img, layer)

    # Clip to the disc so nothing spills outside the circle at any size.
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, S - 1, S - 1), fill=255)
    img.putalpha(Image.composite(img.split()[3], Image.new("L", (S, S), 0), mask))
    return img.resize((size, size), Image.LANCZOS)


