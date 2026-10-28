"""
Split the Heaven & Angel product renders into two transparent layers.

Every render in assets/scent-renders/{male,female}/ is the same studio
shot: the gift box on the left, the 85 ml bottle on the right, on a
coloured backdrop. The public site's collection shows the bottle alone
and slides the box out on hover, so each render is cut into

    static/img/scents/<gender>/<code>-<name>-bottle.webp
    static/img/scents/<gender>/<code>-<name>-box.webp

Both layers share one canvas (same size, same crop), so stacking them at
the same position rebuilds the original shot exactly. A manifest the
React app imports is written next to the layers:

    public-site/src/content/scents.js

The hero's 3D bottle wears the same label: for the scents in SCENE_LABELS
the label is also unwrapped off the cylinder (the render shows it
foreshortened toward the sides) into a flat texture,

    static/img/scene/label-<code>-<name>.webp

and the label's size and placement on the bottle go in the manifest too.

Because the template never changes, the bottle and box masks are built
once (from a male and a female render) and reused for all of them.

    python scripts/build_scent_images.py [--src DIR] [--out DIR] [--no-manifest]

Needs: opencv-python, numpy, pillow (see requirements-dev.txt).
"""
import argparse
import re
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "assets" / "scent-renders"
DEFAULT_OUT = ROOT / "static" / "img" / "scents"
MANIFEST = ROOT / "public-site" / "src" / "content" / "scents.js"

# Masks are built at half the render's resolution (1086 x 1449). Every
# coordinate below is in that space.
WORK_SCALE = 0.5
# Shared canvas both layers are cropped to: x0, y0, x1, y1.
FRAME = (66, 56, 972, 1396)
# The box is cut at the bottle's resolution: it is shown at the same size
# as the bottle, so a smaller file would be stretched and look soft beside it.
BOX_SCALE = 1.0

# Box outline: top-left, top peak, top-right, bottom-right (hidden behind
# the bottle), bottom front corner, bottom-left. Stops above the
# reflection on the floor.
BOX_POLY = [(78, 128), (282, 68), (623, 115), (623, 1333), (280, 1368), (78, 1325)]

# GrabCut seeds for the bottle: search window, probable-bottle window and
# sure-bottle rectangles (cap, collar, body core).
BOTTLE_WINDOW = (565, 108, 971, 1395)
BOTTLE_PROBABLE = (583, 119, 953, 1377)
BOTTLE_SURE = [(703, 167, 833, 319), (674, 377, 862, 507), (616, 601, 920, 1341)]


# The bottles in the hero's 3D scene, and the folder their label textures go in.
SCENE_LABELS = {"H1", "A3"}
SCENE_DIR = ROOT / "static" / "img" / "scene"
# Label rows on the half-size render (top, bottom), and the arc of the
# bottle's front it is unwrapped from. A little under 180 degrees: the
# last few degrees before the silhouette are all but edge-on.
LABEL_ROWS = (576, 1338)
LABEL_ARC_DEG = 164
LABEL_WIDTH = 1024  # texture width; the height follows from the label's shape
# The 3D bottle body is this many times taller than its radius.
BODY_H_OVER_R = 317 / 71.5


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_half(path):
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"cannot read {path}")
    return cv2.resize(img, None, fx=WORK_SCALE, fy=WORK_SCALE, interpolation=cv2.INTER_AREA)


def bottle_mask_from(img):
    """GrabCut the bottle silhouette, then repair it using what we know
    about the shape: each row of a bottle is one solid span, and the
    bottle is symmetric about its centre line (the flag strip on the right
    edge confuses GrabCut, the left edge is clean)."""
    h, w = img.shape[:2]
    gc = np.full((h, w), cv2.GC_BGD, np.uint8)
    x0, y0, x1, y1 = BOTTLE_WINDOW
    gc[y0:y1, x0:x1] = cv2.GC_PR_BGD
    x0, y0, x1, y1 = BOTTLE_PROBABLE
    gc[y0:y1, x0:x1] = cv2.GC_PR_FGD
    for x0, y0, x1, y1 in BOTTLE_SURE:
        gc[y0:y1, x0:x1] = cv2.GC_FGD
    cv2.grabCut(img, gc, None, np.zeros((1, 65)), np.zeros((1, 65)), 8, cv2.GC_INIT_WITH_MASK)
    m = ((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)).astype(np.uint8)

    # Keep only the bottle itself (largest blob).
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m)
    if n > 1:
        m = (labels == 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])).astype(np.uint8)

    ys = np.where(m.any(axis=1))[0]
    left = np.full(h, -1)
    right = np.full(h, -1)
    for y in ys:
        xs = np.where(m[y])[0]
        left[y], right[y] = xs[0], xs[-1]
    rows = ys[(ys > ys[0] + 20) & (ys < ys[0] + 700)]
    cx = float(np.median((left[rows] + right[rows]) / 2.0))
    out = np.zeros_like(m)
    for y in ys:
        lo = min(left[y], int(round(2 * cx - right[y])))
        hi = max(right[y], int(round(2 * cx - left[y])))
        out[y, lo : hi + 1] = 1
    return out


def box_mask_like(shape):
    m = np.zeros(shape[:2], np.uint8)
    cv2.fillPoly(m, [np.array(BOX_POLY, np.int32)], 1)
    return m


def soften(mask, erode=1, blur=0.9):
    """Binary mask -> float alpha with a thin anti-aliased edge, pulled in
    a pixel so the backdrop doesn't ring around the cut-out."""
    if erode:
        mask = cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=erode)
    a = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), blur)
    return np.clip(a, 0, 1)


def build_masks(src):
    male = next((src / "male").glob("*.png"))
    female = next((src / "female").glob("*.png"))
    bottle = bottle_mask_from(load_half(male)) & bottle_mask_from(load_half(female))
    box = box_mask_like(bottle.shape)
    # Box layer = box outline minus the bottle standing in front of it.
    box = box & (1 - cv2.dilate(bottle, np.ones((3, 3), np.uint8)))
    return bottle, box


def bottle_body(bottle_mask):
    """Centre line, radius and base row of the bottle's cylindrical body
    (half-size render pixels)."""
    ys = np.where(bottle_mask.any(axis=1))[0]
    row = np.where(bottle_mask[1000])[0]
    return (row[0] + row[-1]) / 2.0, (row[-1] - row[0] + 1) / 2.0, int(ys[-1])


def label_geometry(bottle_mask):
    """Where the label sits on the 3D bottle: bottom and top as fractions
    of the body's height, and its arc in radians."""
    _, radius, base = bottle_body(bottle_mask)
    body_h = BODY_H_OVER_R * radius
    y0, y1 = LABEL_ROWS
    return (base - y1) / body_h, (base - y0) / body_h, np.radians(LABEL_ARC_DEG)


def unwrap_label(path, bottle_mask):
    """Flatten the label from the render (full size). Each output column is
    an angle round the cylinder, sampled where that angle projects."""
    cx, radius, _ = bottle_body(bottle_mask)
    k = 1 / WORK_SCALE
    cx, radius = cx * k, radius * k
    y0, y1 = (r * k for r in LABEL_ROWS)
    arc = np.radians(LABEL_ARC_DEG)
    out_w = int(round(LABEL_WIDTH * 2))  # sampled at 2x, then reduced
    out_h = int(round(out_w * (y1 - y0) / (arc * radius)))
    theta = (np.arange(out_w) / (out_w - 1) - 0.5) * arc
    map_x = np.tile((cx + radius * np.sin(theta)).astype(np.float32), (out_h, 1))
    map_y = np.tile((y0 + (np.arange(out_h) / (out_h - 1)) * (y1 - y0))[:, None], (1, out_w)).astype(np.float32)
    src = cv2.imread(str(path), cv2.IMREAD_COLOR)
    flat = cv2.remap(src, map_x, map_y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    flat = cv2.resize(flat, (LABEL_WIDTH, round(out_h * LABEL_WIDTH / out_w)), interpolation=cv2.INTER_AREA)
    return Image.fromarray(cv2.cvtColor(flat, cv2.COLOR_BGR2RGB))


def cut_layer(img, alpha, scale):
    x0, y0, x1, y1 = FRAME
    rgba = np.dstack([img, (alpha * 255).astype(np.uint8)])[y0:y1, x0:x1]
    rgba = cv2.cvtColor(rgba, cv2.COLOR_BGRA2RGBA)
    out = Image.fromarray(rgba)
    if scale != 1:
        out = out.resize((round(out.width * scale), round(out.height * scale)), Image.LANCZOS)
    return out


def save_webp(img, path, quality):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "WEBP", quality=quality, method=6, alpha_quality=95)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--no-manifest", action="store_true")
    args = ap.parse_args()

    files = sorted(args.src.glob("*/*.png"))
    if not files:
        sys.exit(f"no renders found in {args.src}")

    bottle_m, box_m = build_masks(args.src)
    bottle_a, box_a = soften(bottle_m), soften(box_m, erode=1)

    entries = []
    for path in files:
        gender = path.parent.name  # male | female
        m = re.match(r"^([A-Za-z]+\d+)_(.+)$", path.stem)
        if not m:
            print(f"  skip  {path.name} (expected <code>_<name>.png)")
            continue
        code, name = m.group(1).upper(), m.group(2).replace("_", " ")
        slug = f"{code.lower()}-{slugify(name)}"
        img = load_half(path)
        save_webp(cut_layer(img, bottle_a, 1.0), args.out / gender / f"{slug}-bottle.webp", 86)
        save_webp(cut_layer(img, box_a, BOX_SCALE), args.out / gender / f"{slug}-box.webp", 80)
        entries.append((gender, code, name, slug))
        if code in SCENE_LABELS:
            save_webp(unwrap_label(path, bottle_m), SCENE_DIR / f"label-{slug}.webp", 90)
        print(f"  ok    {slug}")

    # Layer aspect ratio, for the CSS (both layers share the frame).
    fw, fh = FRAME[2] - FRAME[0], FRAME[3] - FRAME[1]
    # Where the bottle's centre line sits in the frame (0-1): the shelf
    # centres the bottle at rest and slides the frame back on hover.
    ys, xs = np.where(bottle_m)
    cx = ((xs.min() + xs.max()) / 2 - FRAME[0]) / fw

    if not args.no_manifest:
        write_manifest(entries, fw / fh, cx, label_geometry(bottle_m))
    print(f"{len(entries)} scents -> {args.out}")


def write_manifest(entries, aspect, centre, label):
    order = {"female": 0, "male": 1}
    entries = sorted(entries, key=lambda e: (order.get(e[0], 9), int(re.sub(r"\D", "", e[1]))))
    lines = [
        "// GENERATED by scripts/build_scent_images.py. Do not edit by hand.",
        "// One entry per render: the bottle alone and the box beside it, as two",
        "// transparent layers on a shared canvas (see Collection.jsx).",
        "",
        f"export const SCENT_ASPECT = {aspect:.4f}; // layer width / height",
        f"export const SCENT_BOTTLE_CENTRE = {centre:.4f}; // bottle centre line, 0-1 across the layer",
        "",
        "// The label on the hero's 3D bottle: bottom and top as fractions of the",
        "// body's height, and the arc it wraps, in radians (see PerfumeScene.jsx).",
        f"export const SCENE_LABEL = {{ l0: {label[0]:.4f}, l1: {label[1]:.4f}, arc: {label[2]:.4f} }};",
        "",
        "export const SCENTS = [",
    ]
    for gender, code, name, slug in entries:
        base = f"/static/img/scents/{gender}/{slug}"
        variant = "Male" if gender == "male" else "Female"
        lines.append(
            f"  {{ code: '{code}', name: '{name.title()}', variant: '{variant}', "
            f"bottle: '{base}-bottle.webp', box: '{base}-box.webp' }},"
        )
    lines += ["];", ""]
    MANIFEST.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
