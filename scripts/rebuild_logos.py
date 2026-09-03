"""Rebuild the app's logo assets from a single source PNG.

PHASE 14 (YAS-BR-2). The client complained the logo looked framed
and tiny in the sidebar/favicon. Root cause was pixel-level, not
CSS: the source PNG had a hard-white background AND a huge padding
band around the mark (only 27% of the 2000x2000 canvas was actual
logo). This script fixes both:

  1. Load `app/static/img/logo.png` as RGBA
  2. Color-key transparency — any pixel where every channel is
     above the WHITE_THRESHOLD gets alpha=0 (kills the frame
     without hurting the mark)
  3. Auto-crop to the bounding box of visible pixels (kills the
     padding, so the mark now fills the canvas)
  4. Pad to a square with transparent — sidebar/favicon get a
     proper aspect-preserved square, not a portrait mark
  5. Emit four sizes via LANCZOS:
        logo.png      — source-of-truth (trimmed square, same size as
                        the cropped mark, capped to MAX_SOURCE)
        logo-512.png  — hi-res for print/invoice header
        logo-256.png  — sidebar, login, landing
        favicon.png   — browser tab (64x64)

Idempotent: running twice gives the same output because color-key
+ crop converges after the first pass.

If Shalaby later sends a proper transparent, tight-cropped source,
replace `logo.png`, re-run this script, and every derivative
regenerates in one shot.
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "app" / "static" / "img"
SOURCE = IMG / "logo.png"

# A pixel counts as "background" if every RGB channel is above this,
# regardless of its current alpha. 245 tolerates JPEG-ish noise without
# eating into the sky-blue mark itself (which sits well below 240).
WHITE_THRESHOLD = 245

# Cap the source-of-truth file size so re-runs don't drift up.
MAX_SOURCE = 1024

OUTPUTS = {
    "logo-512.png": 512,
    "logo-256.png": 256,
    "favicon.png": 64,
}


def _color_key(im: Image.Image) -> Image.Image:
    """Return a copy where near-white pixels are made transparent."""
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    t = WHITE_THRESHOLD
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if r > t and g > t and b > t:
                px[x, y] = (r, g, b, 0)
    return im


def _crop_to_content(im: Image.Image) -> Image.Image:
    """Crop to the bounding box of pixels with alpha > 0."""
    bbox = im.getbbox()
    return im.crop(bbox) if bbox else im


def _pad_to_square(im: Image.Image) -> Image.Image:
    """Pad shorter side with transparent so we get a square canvas."""
    w, h = im.size
    if w == h:
        return im
    side = max(w, h)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(im, ((side - w) // 2, (side - h) // 2), im)
    return out


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"missing source: {SOURCE}")

    im = Image.open(SOURCE)
    orig_size = im.size
    im = _color_key(im)
    im = _crop_to_content(im)
    cropped_size = im.size
    im = _pad_to_square(im)
    square_side = im.size[0]

    print(f"source={orig_size}  cropped={cropped_size}  square={square_side}")

    # 1. Refresh the source-of-truth (trimmed square, capped size)
    src_out = im
    if square_side > MAX_SOURCE:
        src_out = im.resize((MAX_SOURCE, MAX_SOURCE), Image.LANCZOS)
    src_out.save(SOURCE, format="PNG", optimize=True)
    print(f"wrote {SOURCE.name} at {src_out.size[0]}x{src_out.size[1]}")

    # 2. Emit each derivative
    for fname, size in OUTPUTS.items():
        out = im.resize((size, size), Image.LANCZOS)
        path = IMG / fname
        out.save(path, format="PNG", optimize=True)
        print(f"wrote {fname} at {size}x{size}")


if __name__ == "__main__":
    main()
