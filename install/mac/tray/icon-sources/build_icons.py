"""Generate the ABA Tray icons from a single source-of-truth geometry.

Two outputs:

  ../ABA.app/Contents/Resources/AppIcon.icns
     Full-color rounded-square app icon for the Applications/Spotlight/Dock
     entry. Generated as a full .iconset (16/32/64/128/256/512/1024 + @2x)
     and compiled with `iconutil`.

  ../ABA.app/Contents/Resources/TrayIcon-Template.png       (22x22 base)
  ../ABA.app/Contents/Resources/TrayIcon-Template@2x.png    (44x44)
     Monochrome black-on-transparent **Template image** for the menu bar.
     macOS auto-tints Template images to match the menu-bar background
     (light/dark mode), so this is the only kind of icon that reads
     correctly in both contexts.

Geometry: the BrandIcon SVG from frontend/src/lib/railIcons.tsx — hexagon
with three internal axes meeting at the centre, vertices marked as data
nodes. Reads as a stylised crystal lattice / molecular structure with the
node-at-each-vertex glyph that conveys 'AI / multi-modal compute' without
busy detail.

Run:
    ~/.aba/env/bin/python install/mac/tray/icon-sources/build_icons.py
"""
from __future__ import annotations
import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


# ─── paths ────────────────────────────────────────────────────────────
HERE       = Path(__file__).resolve().parent
APP_BUNDLE = HERE.parent / "ABA.app"
RESOURCES  = APP_BUNDLE / "Contents" / "Resources"
ICONSET    = HERE / "AppIcon.iconset"      # scratch; iconutil consumes it


# ─── hexagon geometry (matches BrandIcon: apex at top) ───────────────
def hex_vertices(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    """Six vertices of a hexagon with the top vertex pointing up. r is the
    distance from centre to a vertex (= circumradius)."""
    pts = []
    for i in range(6):
        angle = math.radians(90 + 60 * i)    # start at top, sweep clockwise (in PIL coords)
        pts.append((cx + r * math.cos(angle), cy - r * math.sin(angle)))
    return pts


def axis_endpoints(verts: list[tuple[float, float]]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Three internal axes — each connects two opposite vertices (0-3, 1-4, 2-5)."""
    return [(verts[i], verts[i + 3]) for i in range(3)]


# ─── colour app icon (1024px master, downsampled for the iconset) ────
def render_app_icon(size: int) -> Image.Image:
    """Full-colour icon. Rounded-square background with a deep-violet → indigo
    radial gradient (scientific-night vibe), centred white hexagon glyph with
    a subtle inner glow, three luminous internal axes, six vertex nodes that
    read as data points / atoms."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Rounded-square background with a radial gradient.
    bg = _radial_gradient(size, inner=(58, 33, 138), outer=(20, 14, 56))
    # Round the corners — macOS uses ~22% corner radius on app icons.
    bg = _round_corners(bg, radius=int(size * 0.22))
    img.paste(bg, (0, 0), bg)

    # 2. Soft outer halo behind the glyph — sells the scientific glow.
    halo_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    halo_draw = ImageDraw.Draw(halo_layer)
    cx = cy = size / 2
    r = size * 0.34
    halo_draw.ellipse([cx - r * 1.05, cy - r * 1.05,
                       cx + r * 1.05, cy + r * 1.05],
                      fill=(150, 180, 255, 70))
    halo_layer = halo_layer.filter(ImageFilter.GaussianBlur(radius=size * 0.06))
    img = Image.alpha_composite(img, halo_layer)
    draw = ImageDraw.Draw(img)

    # 3. The hexagon glyph — bright white outline, very slight inner fill
    # so the hex reads as a translucent crystal.
    verts = hex_vertices(cx, cy, r)
    line_w = max(2, int(size * 0.025))
    # Inner translucent fill (very subtle)
    draw.polygon(verts, fill=(255, 255, 255, 22))
    draw.line(verts + [verts[0]], fill=(255, 255, 255, 245), width=line_w,
              joint="curve")

    # 4. Three internal axes, slightly cooler tint, lower opacity — gives
    # the lattice/3D feel without dominating.
    axis_w = max(1, int(size * 0.015))
    axis_col = (175, 205, 255, 180)
    for a, b in axis_endpoints(verts):
        draw.line([a, b], fill=axis_col, width=axis_w)

    # 5. Vertex nodes (atoms / data points). Two-tone disc — solid bright
    # core, soft halo behind it.
    node_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    nd = ImageDraw.Draw(node_layer)
    node_r = size * 0.028
    for vx, vy in verts:
        nd.ellipse([vx - node_r * 2.6, vy - node_r * 2.6,
                    vx + node_r * 2.6, vy + node_r * 2.6],
                   fill=(170, 210, 255, 95))
    node_layer = node_layer.filter(ImageFilter.GaussianBlur(radius=size * 0.012))
    img = Image.alpha_composite(img, node_layer)
    draw = ImageDraw.Draw(img)
    for vx, vy in verts:
        draw.ellipse([vx - node_r, vy - node_r, vx + node_r, vy + node_r],
                     fill=(255, 255, 255, 255))

    # 6. Central node — slightly bigger, marks the axis intersection.
    centre_r = node_r * 1.4
    draw.ellipse([cx - centre_r, cy - centre_r, cx + centre_r, cy + centre_r],
                 fill=(180, 220, 255, 255))

    return img


def _radial_gradient(size: int, *, inner: tuple[int, int, int],
                     outer: tuple[int, int, int]) -> Image.Image:
    """Cheap two-stop radial gradient — bright at centre, dark at edges."""
    img = Image.new("RGBA", (size, size), outer + (255,))
    px = img.load()
    cx = cy = size / 2
    max_d = math.hypot(cx, cy)
    for y in range(size):
        for x in range(size):
            d = math.hypot(x - cx, y - cy) / max_d
            # easeOutQuad
            t = 1 - (1 - d) ** 2
            r = int(inner[0] * (1 - t) + outer[0] * t)
            g = int(inner[1] * (1 - t) + outer[1] * t)
            b = int(inner[2] * (1 - t) + outer[2] * t)
            px[x, y] = (r, g, b, 255)
    return img


def _round_corners(src: Image.Image, *, radius: int) -> Image.Image:
    """Clip src to a rounded-square. Returns RGBA image with transparent
    corners."""
    w, h = src.size
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, w, h], radius=radius, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(src, (0, 0), mask)
    return out


# ─── monochrome Template glyph for the menu bar ──────────────────────
def render_tray_template(size: int) -> Image.Image:
    """Pure black on transparent, no fill — macOS auto-tints Template images
    to match the menu bar's foreground (light or dark mode). Geometry is the
    same hexagon glyph, no decoration — 22px leaves no room for halos."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Margin so the glyph doesn't touch the menu-bar edge.
    margin = max(1, int(size * 0.10))
    cx = cy = size / 2
    r = (size - 2 * margin) / 2

    verts = hex_vertices(cx, cy, r)

    # Stroke widths tuned per size — Apple's HIG says menu-bar templates
    # should keep ~1.5–2px stroke at base resolution.
    line_w = max(1, int(round(size * 0.07)))
    axis_w = max(1, int(round(size * 0.05)))
    node_r = max(1, int(round(size * 0.07)))

    # Hexagon outline.
    draw.line(verts + [verts[0]], fill=(0, 0, 0, 255), width=line_w,
              joint="curve")
    # Internal axes — slightly thinner so the outline reads first.
    for a, b in axis_endpoints(verts):
        draw.line([a, b], fill=(0, 0, 0, 220), width=axis_w)
    # Vertex nodes.
    for vx, vy in verts:
        draw.ellipse([vx - node_r, vy - node_r, vx + node_r, vy + node_r],
                     fill=(0, 0, 0, 255))
    # Centre node, slightly bigger.
    cr = node_r + 1
    draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=(0, 0, 0, 255))
    return img


# ─── iconset build + iconutil compile ────────────────────────────────
ICONSET_SIZES = [
    # (filename, pixel size)
    ("icon_16x16.png",       16),
    ("icon_16x16@2x.png",    32),
    ("icon_32x32.png",       32),
    ("icon_32x32@2x.png",    64),
    ("icon_128x128.png",    128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png",    256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png",    512),
    ("icon_512x512@2x.png", 1024),
]


def build_app_icns() -> Path:
    """Render every iconset entry, then iconutil compile."""
    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir(parents=True)

    # Render the 1024 master once, downsample for everything else — keeps
    # all sizes visually consistent (no per-size geometry drift).
    master = render_app_icon(1024)
    for name, sz in ICONSET_SIZES:
        if sz == 1024:
            master.save(ICONSET / name, "PNG")
        else:
            master.resize((sz, sz), Image.LANCZOS).save(ICONSET / name, "PNG")

    icns = RESOURCES / "AppIcon.icns"
    RESOURCES.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET), "-o", str(icns)],
        capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"iconutil failed: {res.stderr.strip()}")
    return icns


def build_tray_template() -> tuple[Path, Path]:
    """22x22 base + 44x44 @2x. Apple convention: filename ends with
    `Template` so NSImage knows to auto-tint."""
    one_x = RESOURCES / "TrayIconTemplate.png"
    two_x = RESOURCES / "TrayIconTemplate@2x.png"
    RESOURCES.mkdir(parents=True, exist_ok=True)
    # 44px master, downsampled for the 22px variant — same trick as the
    # iconset, avoids per-size geometry drift.
    master = render_tray_template(44)
    master.save(two_x, "PNG")
    master.resize((22, 22), Image.LANCZOS).save(one_x, "PNG")
    return one_x, two_x


def main() -> int:
    icns = build_app_icns()
    one_x, two_x = build_tray_template()
    print(f"wrote {icns}  ({icns.stat().st_size // 1024} KB)")
    print(f"wrote {one_x}  ({one_x.stat().st_size} B)")
    print(f"wrote {two_x}  ({two_x.stat().st_size} B)")
    # Leave the scratch iconset around — useful for previewing single sizes
    # in Finder. Comment out if you want it cleaned.
    return 0


if __name__ == "__main__":
    sys.exit(main())
