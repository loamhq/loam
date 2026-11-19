#!/usr/bin/env python3
"""One frame for every plugin picture (2026-11-19): a 2x capture with rounded
corners and a soft shadow on a transparent margin, as WebP. The same window
crop, radius and shadow for every screenshot and theme still, so a gallery
reads as one set, in the Market and on GitHub.

    python3 tools/frame-shot.py IN.png OUT.webp [--radius 28] [--margin 40]

Needs Pillow with WebP (an asset tool, never a runtime dependency)."""
import argparse
from PIL import Image, ImageDraw, ImageFilter

ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("out")
ap.add_argument("--radius", type=int, default=28)
ap.add_argument("--margin", type=int, default=40)
ap.add_argument("--quality", type=int, default=86)
a = ap.parse_args()
shot = Image.open(a.src).convert("RGBA")
w, h = shot.size
m, r = a.margin, a.radius
mask = Image.new("L", (w, h), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), r, fill=255)
canvas = Image.new("RGBA", (w + 2 * m, h + 2 * m), (0, 0, 0, 0))
shadow = Image.new("L", canvas.size, 0)
ImageDraw.Draw(shadow).rounded_rectangle((m, m + 10, m + w - 1, m + h + 9), r, fill=70)
shadow = shadow.filter(ImageFilter.GaussianBlur(16))
canvas.paste(Image.new("RGBA", canvas.size, (20, 18, 16, 255)), (0, 0), shadow)
edge = Image.new("L", (w, h), 0)
ImageDraw.Draw(edge).rounded_rectangle((0, 0, w - 1, h - 1), r, outline=40, width=2)
framed = shot.copy()
framed.putalpha(mask)
canvas.alpha_composite(framed, (m, m))
canvas.paste(Image.new("RGBA", (w, h), (20, 18, 16, 255)), (m, m), edge)
canvas.save(a.out, "WEBP", quality=a.quality, method=6)
print(f"{a.out}: {canvas.size[0]}x{canvas.size[1]}")
