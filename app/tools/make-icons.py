#!/usr/bin/env python3
"""Draw the app's mark, and every icon made of it.

The icons in public/ are generated, and this is where they come from: edit the
geometry here and re-run, never the files. A browser wants PNGs of known sizes
before it will offer to install the page, so they are committed rather than
built at start-up, and this draws them with the standard library alone -- a
small scanline rasteriser for the three paths below -- so that making them
needs nothing installed.

They are drawn finely because one of them is drawn large: Android's splash
screen puts the manifest icon closest to the screen's resolution in the middle
of the page, and the owner, 2026-10-01, saw the old ones' facets there. Every
cubic is flattened to CURVE_STEPS segments, and every pixel is covered exactly
across its width on SUBSAMPLE scanlines down it -- finer than a 16x16
supersample. The output is the same bytes on every run.

    python3 tools/make-icons.py            # into public/
    python3 tools/make-icons.py <dir>      # somewhere else, to compare

The mark is "Strata": three layers of earth cut to the same organic blob the
agents are drawn in, on a 64-unit square, with its own fills so it reads the
same on either theme. A placeholder, as the product's name is.

What it writes:

    icon.svg                 the mark, transparent round the blob: tab, rail
    icon-32.png              the same, for a browser that will not take an SVG favicon
    icon-192.png, -512.png,  the same, the manifest's "any" icons; 1024 for a
      -1024.png                high-density splash screen
    icon-maskable-192.png,   the mark on the ground colour, inside the safe zone,
      -512.png, -1024.png      for Android's own mask
    apple-touch-icon.png     180px, opaque, for the iOS home screen
"""

import math
import struct
import sys
import zlib
from pathlib import Path

PUBLIC = Path(__file__).resolve().parent.parent / "public"

VIEW = 64
BLOB = "M34 3 C52 3 61 15 60 33 C59 51 49 61 31 61 C13 61 3 50 4 32 C5 14 16 3 34 3 Z"
LAYERS = [
    # (fill, path), bottom to top. The first is the whole square: the blob clips it.
    ("#CDA57F", "M0 0 H64 V64 H0 Z"),
    ("#7A5236", "M0 27 C12 21 22 33 34 27 S54 23 64 29 V64 H0 Z"),
    ("#4A3A2E", "M0 45 C14 39 24 51 36 45 S56 41 64 47 V64 H0 Z"),
]
GROUND = "#E6E1DA"  # the light theme's page, --c1

# A maskable icon's safe zone is the circle of 40% of its width round the
# centre. The mark is scaled so the farthest point of the blob sits at this
# fraction of the width from the centre: inside the zone, with a margin.
MASKABLE_RADIUS = 0.36

# How finely the mark is drawn: segments per cubic, and scanlines per pixel row.
CURVE_STEPS = 256
SUBSAMPLE = 16


def rgb(hex_colour):
    return tuple(int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))


# ------------------------------------------------------------------ paths


def tokens(d):
    out, num = [], ""
    for ch in d + " ":
        if ch.isalpha() or ch in " ,":
            if num:
                out.append(float(num))
                num = ""
            if ch.isalpha():
                out.append(ch)
        else:
            num += ch
    return out


def polygons(d, steps=CURVE_STEPS):
    """The path as closed polygons, cubics flattened. Absolute M C S H V Z only,
    which is all the mark uses -- anything else is refused rather than guessed."""
    t = tokens(d)
    i, polys, poly = 0, [], []
    x = y = 0.0
    ctrl = None  # the last cubic's second control point, for S
    cmd = None
    while i < len(t):
        if isinstance(t[i], str):
            cmd = t[i]
            i += 1
            if cmd == "Z":
                if poly:
                    polys.append(poly)
                poly, ctrl = [], None
                continue
        if cmd == "M":
            if poly:
                polys.append(poly)
            x, y = t[i], t[i + 1]
            poly, ctrl = [(x, y)], None
            i += 2
            cmd = "L"
        elif cmd == "L":
            x, y = t[i], t[i + 1]
            poly.append((x, y))
            ctrl, i = None, i + 2
        elif cmd == "H":
            x = t[i]
            poly.append((x, y))
            ctrl, i = None, i + 1
        elif cmd == "V":
            y = t[i]
            poly.append((x, y))
            ctrl, i = None, i + 1
        elif cmd in "CS":
            if cmd == "C":
                x1, y1, x2, y2, ex, ey = t[i : i + 6]
                i += 6
            else:
                x1, y1 = (2 * x - ctrl[0], 2 * y - ctrl[1]) if ctrl else (x, y)
                x2, y2, ex, ey = t[i : i + 4]
                i += 4
            for s in range(1, steps + 1):
                u = s / steps
                v = 1 - u
                poly.append((
                    v**3 * x + 3 * v * v * u * x1 + 3 * v * u * u * x2 + u**3 * ex,
                    v**3 * y + 3 * v * v * u * y1 + 3 * v * u * u * y2 + u**3 * ey,
                ))
            ctrl = (x2, y2)
            x, y = ex, ey
        else:
            raise ValueError(f"path command {cmd!r} is not one this draws")
    if poly:
        polys.append(poly)
    return polys


def coverage(polys, size, scale, offset, sub=SUBSAMPLE):
    """How much of each pixel the shape covers, 0..1: `sub` scanlines a pixel,
    each span covered exactly across the pixels it crosses, its ends by the
    fraction of the pixel they reach. Even-odd, which the mark's paths agree with."""
    rows = [[] for _ in range(size)]  # the edges each pixel row can meet
    for poly in polys:
        pts = [(px * scale + offset, py * scale + offset) for px, py in poly]
        for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
            if ay == by:
                continue
            edge = (ax, ay, (bx - ax) / (by - ay), min(ay, by), max(ay, by))
            for row in range(max(int(edge[3]), 0), min(int(edge[4]), size - 1) + 1):
                rows[row].append(edge)
    cov = [0.0] * (size * size)
    w = 1.0 / sub
    for row in range(size):
        base = row * size
        edges = rows[row]
        for k in range(sub):
            sy = row + (k + 0.5) / sub
            xs = sorted(ax + (sy - ay) * slope for ax, ay, slope, lo, hi in edges if lo <= sy < hi)
            for a, b in zip(xs[0::2], xs[1::2]):
                a, b = max(a, 0.0), min(b, float(size))
                if b <= a:
                    continue
                ia, ib = int(a), min(int(b), size - 1)
                if ia == ib:
                    cov[base + ia] += (b - a) * w
                    continue
                cov[base + ia] += (ia + 1 - a) * w
                for px in range(ia + 1, ib):
                    cov[base + px] += w
                if ib < size:
                    cov[base + ib] += (b - ib) * w
    return [min(c, 1.0) for c in cov]


def blob_radius():
    """The farthest point of the blob from the square's centre, in view units."""
    c = VIEW / 2
    return max(math.hypot(px - c, py - c) for poly in polygons(BLOB) for px, py in poly)


# ------------------------------------------------------------------ drawing


def draw(size, ground=None):
    """The mark at `size` pixels: filling the square when there is no ground,
    otherwise on that colour and scaled into a maskable icon's safe zone."""
    if ground is None:
        scale, offset = size / VIEW, 0.0
    else:
        scale = MASKABLE_RADIUS * size / blob_radius()
        offset = size / 2 - scale * VIEW / 2
    clip = coverage(polygons(BLOB), size, scale, offset)
    layers = [(rgb(fill), coverage(polygons(d), size, scale, offset)) for fill, d in LAYERS]

    out = bytearray()
    for p in range(size * size):
        r = g = b = 0.0
        for (lr, lg, lb), cov in layers:
            a = cov[p]
            r, g, b = r + (lr - r) * a, g + (lg - g) * a, b + (lb - b) * a
        a = clip[p]
        if ground is None:
            out += bytes((round(r), round(g), round(b), round(a * 255)))
        else:
            gr, gg, gb = rgb(ground)
            out += bytes((round(gr + (r - gr) * a), round(gg + (g - gg) * a), round(gb + (b - gb) * a), 255))
    return [bytes(out[y * size * 4 : (y + 1) * size * 4]) for y in range(size)]


def png(size, rows):
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def svg():
    fills = "\n".join(
        f'    <rect width="{VIEW}" height="{VIEW}" fill="{fill}"/>' if i == 0 else f'    <path d="{d}" fill="{fill}"/>'
        for i, (fill, d) in enumerate(LAYERS)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEW} {VIEW}">\n'
        "  <defs>\n"
        f'    <clipPath id="loam-blob"><path d="{BLOB}"/></clipPath>\n'
        "  </defs>\n"
        '  <g clip-path="url(#loam-blob)">\n'
        f"{fills}\n"
        "  </g>\n"
        "</svg>\n"
    )


def main():
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else PUBLIC
    out = target / "icon.svg"
    out.write_text(svg())
    print(f"wrote {out}")
    for name, size, ground in [
        ("icon-32.png", 32, None),
        ("icon-192.png", 192, None),
        ("icon-512.png", 512, None),
        ("icon-1024.png", 1024, None),
        ("icon-maskable-192.png", 192, GROUND),
        ("icon-maskable-512.png", 512, GROUND),
        ("icon-maskable-1024.png", 1024, GROUND),
        ("apple-touch-icon.png", 180, GROUND),
    ]:
        out = target / name
        out.write_bytes(png(size, draw(size, ground)))
        print(f"wrote {out} ({out.stat().st_size} bytes)")
    print(f"blob reaches {blob_radius() / VIEW:.3f} of the width from the centre; "
          f"maskable icons hold it at {MASKABLE_RADIUS} (safe zone 0.40)")


if __name__ == "__main__":
    main()
