#!/usr/bin/env python3
"""Build the Rantasalo & Co. wrap for a Model 3 (2017-2023).

Renders the RANTASALO & Co. wordmark from the source PDF, inverts it to
white, and lays it out on the hood panel of model3/template.png.

    pip install pillow numpy pymupdf
    python3 build_wrap.py

Outputs Rantasalo_Black.png (the wrap), Rantasalo_Logo_White.png (the
inverted wordmark on transparency) and preview.png (the wrap with the
template outline on top, to check panel alignment).
"""

from collections import deque
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "template.png"
LOGO_PDF = HERE / "rantasaloco_logo.pdf"

SIZE = 1024                 # texture is 1024x1024, same as the template
BACKGROUND = (0, 0, 0)      # gloss black over every panel
INK = (255, 255, 255)       # wordmark colour
OUTLINE = (74, 74, 74)      # panel outlines, preview.png only

LOGO_WIDTH = 180            # wordmark width in texture pixels (~62% of hood width)
LOGO_CENTER_Y = 293         # texture row the wordmark is centred on
HOOD_CENTER_X = 511.5       # the hood UV island is symmetric about this column


def panel_masks(path):
    """Label every enclosed panel in the template.

    The template is white panels with black outlines on a transparent
    background, so 'not ink' picks out exactly the panel interiors.
    """
    rgba = np.array(Image.open(path).convert("RGBA"))
    open_px = (rgba[..., :3].astype(int).sum(-1) >= 400) & (rgba[..., 3] > 0)

    h, w = open_px.shape
    label = np.where(open_px, -1, -2).astype(np.int32)
    masks = []
    for sy in range(h):
        for sx in range(w):
            if label[sy, sx] != -1:
                continue
            idx = len(masks)
            queue = deque([(sy, sx)])
            label[sy, sx] = idx
            while queue:
                y, x = queue.popleft()
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < h and 0 <= nx < w and label[ny, nx] == -1:
                        label[ny, nx] = idx
                        queue.append((ny, nx))
            masks.append(label == idx)
    return masks


def hood_mask(path):
    """The hood is the panel just below the front fascia on the centre line.

    Centre-line islands run front to back down the middle of the texture:
    fascia, hood, (glass roof), rear deck, rear bumper. The hood is the
    upper of the two tall centre islands.
    """
    centre = []
    for mask in panel_masks(path):
        ys, xs = np.where(mask)
        if abs((xs.min() + xs.max()) / 2 - SIZE / 2) < 20 and np.ptp(ys) > 150:
            centre.append((ys.min(), mask))
    centre.sort(key=lambda item: item[0])
    return centre[0][1]


def logo_alpha(pdf_path, scale=8):
    """Render the wordmark and return its coverage, trimmed to the ink."""
    page = pymupdf.open(pdf_path)[0]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=True)
    raw = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)

    alpha = raw[..., 3].astype(np.float64) / 255.0
    luma = raw[..., :3].astype(np.float64).mean(-1) / 255.0
    coverage = alpha * (1.0 - luma)          # 1 where the mark is solid

    ys, xs = np.where(coverage > 0.004)
    coverage = coverage[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return coverage / coverage.max()


def main():
    hood = hood_mask(TEMPLATE)
    ys, xs = np.where(hood)
    print(f"hood island: x {xs.min()}-{xs.max()}, y {ys.min()}-{ys.max()}")

    coverage = logo_alpha(LOGO_PDF)
    aspect = coverage.shape[1] / coverage.shape[0]
    height = max(1, round(LOGO_WIDTH / aspect))

    # Sits on the hood the right way up for someone standing in front of the
    # car: the nose is the top of the UV island, so the mark is rotated 180.
    mark = Image.fromarray((coverage * 255).astype(np.uint8), "L")
    mark = mark.resize((LOGO_WIDTH, height), Image.LANCZOS).rotate(180)

    x0 = int(round(HOOD_CENTER_X - LOGO_WIDTH / 2))
    y0 = int(round(LOGO_CENTER_Y - height / 2))

    mask = np.zeros((SIZE, SIZE), np.float64)
    mask[y0:y0 + height, x0:x0 + LOGO_WIDTH] = np.array(mark) / 255.0

    if not hood[mask > 0.02].all():
        raise SystemExit("wordmark spills off the hood panel")
    print(f"wordmark: {LOGO_WIDTH}x{height} at ({x0}, {y0})")

    wrap = np.zeros((SIZE, SIZE, 3), np.float64)
    wrap[:] = BACKGROUND
    wrap += mask[..., None] * (np.array(INK, float) - BACKGROUND)
    wrap = Image.fromarray(np.rint(wrap).astype(np.uint8), "RGB")
    wrap.save(HERE / "Rantasalo_Black.png", optimize=True)

    white = np.zeros(coverage.shape + (4,), np.uint8)
    white[..., :3] = 255
    white[..., 3] = np.rint(coverage * 255).astype(np.uint8)
    Image.fromarray(white, "RGBA").save(HERE / "Rantasalo_Logo_White.png", optimize=True)

    # Panel outlines drawn over the wrap, so the layout stays readable on black.
    guide = np.array(Image.open(TEMPLATE).convert("RGBA")).astype(np.float64)
    stroke = (guide[..., 3] / 255.0) * (1.0 - guide[..., :3].mean(-1) / 255.0)
    preview = np.array(wrap, np.float64)
    preview += stroke[..., None] * (np.array(OUTLINE, float) - preview)
    Image.fromarray(np.rint(preview).astype(np.uint8), "RGB").save(
        HERE / "preview.png", optimize=True
    )

    # The hood as it reads to someone standing in front of the car.
    detail = wrap.crop((350, 155, 675, 420)).rotate(180)
    detail = detail.resize((detail.width * 3, detail.height * 3), Image.LANCZOS)
    detail.save(HERE / "preview_hood.png", optimize=True)

    for name in ("Rantasalo_Black.png", "Rantasalo_Logo_White.png",
                 "preview.png", "preview_hood.png"):
        print(f"  {name}: {(HERE / name).stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
