"""Rebuild the Proxious logo as pixel-exact SVG (light + dark) and render every icon Open WebUI needs.

The source (brand/source/proxious-light.jpg, 2000x2000) is pixel art upscaled from a ~1000 px grid with halftone dots.
We threshold it onto that grid, trace the exact outline of every ink region (pixel-boundary loops, collinear edges
merged), and emit one <path> per variant. Fill rule nonzero works because every loop is wound consistently.
Usage: python build_brand.py [out_dir]   (needs Pillow + numpy)
"""
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
SRC = HERE / "source" / "proxious-light.jpg"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "out"
GRID = 1000                  # 2000 px source / 2 ; best fidelity vs. noise (97.4% pixel match)
INK_LIGHT = "#0a0a0a"        # light theme: near-black ink on transparent
INK_DARK = "#f4f4f5"         # dark theme: near-white ink on transparent

# --------------------------------------------------------------------------- grid
src = Image.open(SRC).convert("L")
g = np.asarray(src.resize((GRID, GRID), Image.BOX), dtype=float) / 255.0
ink = g < 0.5                # True = ink
H, W = ink.shape


def bbox(mask):
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


# split emblem / wordmark by the first fully empty row band below the emblem
rows = ink.any(axis=1)
emblem_end = None
y = int(H * 0.6)
while y < H:
    if not rows[y]:
        run = y
        while run < H and not rows[run]:
            run += 1
        if run - y >= 8:     # a real gap, not a hairline
            emblem_end = y
            break
        y = run
    y += 1
mark_mask = ink.copy(); mark_mask[emblem_end:, :] = False
word_mask = ink.copy(); word_mask[:emblem_end, :] = False


# --------------------------------------------------------------------------- tracing
def trace(mask):
    """Exact boundary loops of the True cells, as SVG path data (integers, h/v commands)."""
    m = np.pad(mask, 1)
    c = m[1:-1, 1:-1]
    # boundary edges, wound clockwise in screen coords (y down): top, right, bottom, left
    nxt = {}
    def add(x0, y0, x1, y1):
        for a, b, cc, d in zip(x0, y0, x1, y1):
            nxt.setdefault((a, b), []).append((cc, d))
    ys, xs = np.nonzero(c & ~m[:-2, 1:-1]);  add(xs, ys, xs + 1, ys)             # top
    ys, xs = np.nonzero(c & ~m[1:-1, 2:]);   add(xs + 1, ys, xs + 1, ys + 1)     # right
    ys, xs = np.nonzero(c & ~m[2:, 1:-1]);   add(xs + 1, ys + 1, xs, ys + 1)     # bottom
    ys, xs = np.nonzero(c & ~m[1:-1, :-2]);  add(xs, ys + 1, xs, ys)             # left
    out = []
    for start in sorted(nxt):
        while nxt.get(start):
            pts = [start]
            cur = nxt[start].pop()
            while cur != start:
                pts.append(cur)
                cur = nxt[cur].pop()
            # merge collinear points
            simp = []
            n = len(pts)
            for i in range(n):
                p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
                if (p1[0] - p0[0]) * (p2[1] - p1[1]) != (p1[1] - p0[1]) * (p2[0] - p1[0]):
                    simp.append(p1)
            seg = [f"M{simp[0][0]} {simp[0][1]}"]
            for (ax, ay), (bx, by) in zip(simp, simp[1:]):
                seg.append(f"h{bx - ax}" if by == ay else f"v{by - ay}")
            seg.append("z")
            out.append("".join(seg))
    return "".join(out)


def svg(mask, pad, ink_color, title, adaptive=False):
    x0, y0, x1, y1 = bbox(mask)
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    w, h = x1 - x0, y1 - y0
    d = trace(mask)
    style = ""
    fill = f' fill="{ink_color}"'
    if adaptive:  # favicon: follows the OS / browser theme by itself
        style = (f"<style>path{{fill:{INK_LIGHT}}}@media (prefers-color-scheme:dark){{path{{fill:{INK_DARK}}}}}</style>")
        fill = ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0} {y0} {w} {h}" role="img" aria-label="{title}">'
            f"<title>{title}</title>{style}<path{fill} d=\"{d}\"/></svg>\n")


# --------------------------------------------------------------------------- rasters
hi = np.asarray(src, dtype=float) / 255.0          # 2000 px source for smooth downsampling
hi_ink = (hi < 0.5).astype(float)
S = 2000 / GRID


def raster(mask_region, size, ink_rgb, bg=None, pad_frac=0.0, circle_bg=False):
    """Render a region of the logo to a size x size RGBA icon (coverage-based antialiasing)."""
    x0, y0, x1, y1 = bbox(mask_region)
    X0, Y0, X1, Y1 = int(x0 * S), int(y0 * S), int(x1 * S), int(y1 * S)
    region = np.zeros_like(hi_ink)
    region[Y0:Y1, X0:X1] = hi_ink[Y0:Y1, X0:X1]
    crop = region[Y0:Y1, X0:X1]
    side = max(crop.shape)
    sq = np.zeros((side, side))
    oy, ox = (side - crop.shape[0]) // 2, (side - crop.shape[1]) // 2
    sq[oy:oy + crop.shape[0], ox:ox + crop.shape[1]] = crop
    inner = max(1, int(round(size * (1 - 2 * pad_frac))))
    a = Image.fromarray((sq * 255).astype("uint8")).resize((inner, inner), Image.LANCZOS)
    alpha = Image.new("L", (size, size), 0)
    off = (size - inner) // 2
    alpha.paste(a, (off, off))
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if bg is not None:
        if circle_bg:
            from PIL import ImageDraw
            bgl = Image.new("L", (size * 4, size * 4), 0)
            ImageDraw.Draw(bgl).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
            img.paste(Image.new("RGBA", (size, size), bg + (255,)), (0, 0), bgl.resize((size, size), Image.LANCZOS))
        else:
            img = Image.new("RGBA", (size, size), bg + (255,))
    img.paste(Image.new("RGBA", (size, size), ink_rgb + (255,)), (0, 0), alpha)
    return img


def save_png(img, path):
    """Single ink colour + background -> a 64-colour palette is visually lossless and ~4-6x smaller."""
    img.quantize(colors=64, method=Image.Quantize.FASTOCTREE).save(path, optimize=True)


def rgb(hexc):
    return tuple(int(hexc[i:i + 2], 16) for i in (1, 3, 5))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "svg").mkdir(exist_ok=True)
    (OUT / "static").mkdir(exist_ok=True)
    L, D = rgb(INK_LIGHT), rgb(INK_DARK)
    WHITE, BLACK = (255, 255, 255), (13, 13, 13)

    files = {
        "svg/proxious-logo-light.svg": svg(ink, 12, INK_LIGHT, "Proxious"),
        "svg/proxious-logo-dark.svg": svg(ink, 12, INK_DARK, "Proxious"),
        "svg/proxious-mark-light.svg": svg(mark_mask, 8, INK_LIGHT, "Proxious"),
        "svg/proxious-mark-dark.svg": svg(mark_mask, 8, INK_DARK, "Proxious"),
        "svg/proxious-wordmark-light.svg": svg(word_mask, 4, INK_LIGHT, "Proxious"),
        "svg/proxious-wordmark-dark.svg": svg(word_mask, 4, INK_DARK, "Proxious"),
        "static/favicon.svg": svg(mark_mask, 8, INK_LIGHT, "Proxious", adaptive=True),
    }
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")

    st = OUT / "static"
    # Open WebUI's own filenames (it references these ~90 times: tab, sidebar, login, model avatars)
    # Coins (emblem on a disc): Open WebUI also serves favicon.png through endpoints brand.js can't see (model
    # profile images), so the light file must stay visible on dark backgrounds too. brand.js swaps where it can.
    save_png(raster(mark_mask, 512, L, WHITE, 0.03, circle_bg=True), st / "favicon.png")
    save_png(raster(mark_mask, 512, D, BLACK, 0.03, circle_bg=True), st / "favicon-dark.png")
    save_png(raster(mark_mask, 96, L, WHITE, 0.04, circle_bg=True), st / "favicon-96x96.png")   # browser tab: coin
    save_png(raster(mark_mask, 180, L, WHITE, 0.08), st / "apple-touch-icon.png")                # iOS: opaque square
    save_png(raster(mark_mask, 192, L, WHITE, 0.12), st / "web-app-manifest-192x192.png")       # maskable safe zone
    save_png(raster(mark_mask, 512, L, WHITE, 0.12), st / "web-app-manifest-512x512.png")
    save_png(raster(ink, 500, L, None, 0.04), st / "splash.png")                  # loading screen, light theme
    save_png(raster(ink, 500, D, None, 0.04), st / "splash-dark.png")             # loading screen, dark theme
    save_png(raster(mark_mask, 500, L, WHITE, 0.12), st / "logo.png")  # PWA icon: manifest uses it as any + maskable
    ico_imgs = [raster(mark_mask, s, L, WHITE, 0.02, circle_bg=True) for s in (16, 32, 48, 64)]
    ico_imgs[-1].save(st / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)], append_images=ico_imgs[:-1])
    report = {n: (OUT / n).stat().st_size for n in files}
    report.update({f"static/{p.name}": p.stat().st_size for p in st.glob("*.png")})
    report["static/favicon.ico"] = (st / "favicon.ico").stat().st_size
    print(json.dumps(report, indent=1))
    print("emblem rows 0..", emblem_end, "| grid", GRID)


if __name__ == "__main__":
    main()
