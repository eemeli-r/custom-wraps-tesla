#!/usr/bin/env python3
"""Build the Rantasalo & Co. wraps for a Model 3 (2017-2023).

Renders the RANTASALO & Co. wordmark from the source PDF, inverts it to
white, and lays it out on model3/template.png in two variants: the mark on
the hood, or the mark on the left and right front doors.

    pip install pillow numpy pymupdf
    python3 build_wrap.py

Writes both wraps, the inverted wordmark for reuse, and previews.
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

# Gloss black over every panel. Not #000000: at zero the diffuse term is dead
# and the body renders as a flat silhouette with no visible form. #1A1A1C is
# about 1% linear reflectance, which is where real jet black basecoat sits, and
# the last channel is lifted a touch because automotive blacks read slightly
# cool rather than dead neutral.
BACKGROUND = (26, 26, 28)
INK = (255, 255, 255)       # wordmark colour
OUTLINE = (92, 92, 92)      # panel outlines, previews only

# Wordmark length in texture pixels, shared by both variants so they read at
# the same physical size on the car: the hood island and the door islands work
# out to roughly the same pixels-per-millimetre.
LOGO_LENGTH = 153

HOOD_CENTER_Y = 293         # texture row the hood mark is centred on
SIDE_HEIGHT_FRAC = 0.55     # up the door, 0 at the rocker and 1 at the belt line

# PIL rotates counter-clockwise. The nose of the car is the top of the hood
# island, so the hood mark is turned 180 to read the right way up from in front
# of the car. The sides are laid out with the front of the car at the top and
# the roof towards the middle of the texture, which puts the two doors in
# opposite quarter turns: each one reads left to right for somebody standing on
# that side of the car.
TURN_HOOD, TURN_LEFT, TURN_RIGHT = 180, -90, 90


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


def find_panels(path):
    """Pick out the hood and the two front doors.

    Centre-line islands run front to back down the middle of the texture:
    fascia, hood, (glass roof), rear deck, rear bumper. The sides are stacked
    down each edge, front door above rear door.
    """
    hood, doors = [], []
    for mask in panel_masks(path):
        ys, xs = np.where(mask)
        cx, top, height = (xs.min() + xs.max()) / 2, ys.min(), np.ptp(ys)

        if abs(cx - SIZE / 2) < 20 and height > 150:
            hood.append((top, mask))
        elif abs(cx - SIZE / 2) > 200 and height < 300 and len(ys) > 20000:
            doors.append((top, cx, mask))

    hood.sort(key=lambda item: item[0])
    doors.sort(key=lambda item: item[0])
    front = doors[:2]                              # front doors sit above rear
    left = min(front, key=lambda item: item[1])[2]
    right = max(front, key=lambda item: item[1])[2]
    return hood[0][1], left, right


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


def hood_anchor(panel):
    """Centre line of the hood island, at the chosen row."""
    xs = np.where(panel[HOOD_CENTER_Y])[0]
    return (xs.min() + xs.max()) / 2, HOOD_CENTER_Y


def door_anchor(panel, frac=SIDE_HEIGHT_FRAC):
    """A point part way up a door, centred along the length of the car.

    Columns are heights on the car. Whichever edge of the door sits nearer the
    middle of the texture is the belt line; the outer edge is the rocker.
    """
    cols = np.where(panel.any(0))[0]
    runs = {x: np.ptp(np.where(panel[:, x])[0]) + 1 for x in cols}
    band = [x for x in cols if runs[x] >= 0.9 * max(runs.values())]

    lo, hi = min(band), max(band)
    belt, rocker = (lo, hi) if abs(lo - SIZE / 2) < abs(hi - SIZE / 2) else (hi, lo)

    x = int(round(rocker + frac * (belt - rocker)))
    rows = np.where(panel[:, x])[0]
    return x, (rows.min() + rows.max()) / 2


def stamp(canvas, coverage, turn, anchor, panel, what):
    """Paint the wordmark into a coverage canvas, centred on anchor."""
    aspect = coverage.shape[1] / coverage.shape[0]
    thickness = max(1, round(LOGO_LENGTH / aspect))

    mark = Image.fromarray((coverage * 255).astype(np.uint8), "L")
    mark = mark.resize((LOGO_LENGTH, thickness), Image.LANCZOS).rotate(turn, expand=True)
    art = np.array(mark).astype(np.float64) / 255.0

    h, w = art.shape
    x0 = int(round(anchor[0] - w / 2))
    y0 = int(round(anchor[1] - h / 2))
    window = canvas[y0:y0 + h, x0:x0 + w]
    np.maximum(window, art, out=window)

    spill = np.zeros_like(canvas, bool)
    spill[y0:y0 + h, x0:x0 + w] = art > 0.02
    if not panel[spill].all():
        raise SystemExit(f"wordmark spills off the {what} panel")
    print(f"  {what}: {LOGO_LENGTH}x{thickness} at ({x0}, {y0})")


def render(canvas):
    wrap = np.empty((SIZE, SIZE, 3), np.float64)
    wrap[:] = BACKGROUND
    wrap += canvas[..., None] * (np.array(INK, float) - BACKGROUND)
    return Image.fromarray(np.rint(wrap).astype(np.uint8), "RGB")


def guide_preview(wrap, stroke):
    """The wrap with the template's panel outlines drawn over it."""
    out = np.array(wrap, np.float64)
    out += stroke[..., None] * (np.array(OUTLINE, float) - out)
    return Image.fromarray(np.rint(out).astype(np.uint8), "RGB")


def zoom(image, box, turn, scale=3):
    crop = image.crop(box).rotate(turn, expand=True)
    return crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)


def stack(images, gap=12):
    width = max(i.width for i in images)
    height = sum(i.height for i in images) + gap * (len(images) - 1)
    sheet = Image.new("RGB", (width, height), BACKGROUND)
    y = 0
    for image in images:
        sheet.paste(image, ((width - image.width) // 2, y))
        y += image.height + gap
    return sheet


def main():
    hood, left_door, right_door = find_panels(TEMPLATE)
    coverage = logo_alpha(LOGO_PDF)

    print("hood variant")
    canvas = np.zeros((SIZE, SIZE))
    stamp(canvas, coverage, TURN_HOOD, hood_anchor(hood), hood, "hood")
    hood_wrap = render(canvas)
    hood_wrap.save(HERE / "Rantasalo_Black.png", optimize=True)

    print("sides variant")
    canvas = np.zeros((SIZE, SIZE))
    stamp(canvas, coverage, TURN_LEFT, door_anchor(left_door), left_door, "left door")
    stamp(canvas, coverage, TURN_RIGHT, door_anchor(right_door), right_door, "right door")
    sides_wrap = render(canvas)
    sides_wrap.save(HERE / "Rantasalo_Sides.png", optimize=True)

    white = np.zeros(coverage.shape + (4,), np.uint8)
    white[..., :3] = 255
    white[..., 3] = np.rint(coverage * 255).astype(np.uint8)
    Image.fromarray(white, "RGBA").save(HERE / "Rantasalo_Logo_White.png", optimize=True)

    guide = np.array(Image.open(TEMPLATE).convert("RGBA")).astype(np.float64)
    stroke = (guide[..., 3] / 255.0) * (1.0 - guide[..., :3].mean(-1) / 255.0)
    guide_preview(hood_wrap, stroke).save(HERE / "preview.png", optimize=True)
    guide_preview(sides_wrap, stroke).save(HERE / "preview_sides.png", optimize=True)

    # The hood as it reads to someone standing in front of the car, and each
    # side as it reads to someone standing beside it. The doors keep their
    # outlines, because the point of that shot is where the mark sits on them.
    zoom(hood_wrap, (350, 155, 675, 420), 180).save(HERE / "preview_hood.png", optimize=True)
    doors = guide_preview(sides_wrap, stroke)
    stack([
        zoom(doors, (20, 350, 240, 860), 90, scale=2),      # left side of car
        zoom(doors, (784, 350, 1004, 860), -90, scale=2),   # right side of car
    ]).save(HERE / "preview_doors.png", optimize=True)

    for name in ("Rantasalo_Black.png", "Rantasalo_Sides.png", "Rantasalo_Logo_White.png",
                 "preview.png", "preview_sides.png", "preview_hood.png", "preview_doors.png"):
        print(f"  {name}: {(HERE / name).stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
