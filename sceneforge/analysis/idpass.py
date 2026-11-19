from __future__ import annotations

import io
import json

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from sceneforge.analysis.jslex import LexError, line_of, new_calls
from sceneforge.pipeline.assets import JS_DIR
from sceneforge.pipeline.wrapper import ASSET_CALL, EXPORT_LINE, normalize

LEVELS = (0, 85, 170, 255)
PALETTE = [(r, g, b) for r in LEVELS for g in LEVELS for b in LEVELS if (r, g, b) != (255, 255, 255)]
CODES = {r * 65536 + g * 256 + b: key for key, (r, g, b) in enumerate(PALETTE)}
_LUT = np.array(sorted(CODES))
_LUT_KEYS = np.array([CODES[code] for code in _LUT])
ID_JS = (JS_DIR / "idpass.js").read_text(encoding="utf-8")
GEOMETRIES = ("BufferGeometry", "BoxGeometry", "SphereGeometry", "CylinderGeometry", "CapsuleGeometry", "ConeGeometry",
              "TorusGeometry", "TorusKnotGeometry", "PlaneGeometry", "CircleGeometry", "RingGeometry",
              "TetrahedronGeometry", "OctahedronGeometry", "DodecahedronGeometry", "IcosahedronGeometry",
              "PolyhedronGeometry", "ExtrudeGeometry", "LatheGeometry", "ShapeGeometry", "TubeGeometry")
SITE_HELPER = "function __site404(g, k) { if (g && g.userData) g.userData.site = k; return g; }\n"


def tag_sites(raw: str) -> tuple[str, dict[int, int]] | None:
    try:
        calls = new_calls(raw, GEOMETRIES)
    except LexError:
        return None
    lines: dict[int, int] = {}
    out = raw
    for key, (start, end, _) in reversed(list(enumerate(calls, start=1))):
        if key >= len(PALETTE):
            continue
        lines[key] = line_of(raw, start)
        out = out[:start] + "__site404(" + out[start:end] + f", {key})" + out[end:]
    return SITE_HELPER + out, lines


def id_module(raw: str, wrapper: str, mode: str, see_through: bool = False) -> str:
    palette = "[" + ",".join(f"0x{r:02x}{g:02x}{b:02x}" for r, g, b in PALETTE) + "]"
    pass_js = (ID_JS.replace("__PALETTE__", palette).replace("__MODE__", json.dumps(mode))
               .replace("__SEETHROUGH__", "true" if see_through else "false"))
    wrapped = wrapper.replace(ASSET_CALL, ASSET_CALL + "\n  __idpass404(THREE, root);", 1)
    wrapped = wrapped.replace(EXPORT_LINE, pass_js + "\n" + EXPORT_LINE, 1)
    return normalize(raw, wrapped)


def decode(png: bytes, gap: int = 0) -> list[np.ndarray]:
    image = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(np.int64)
    code = image[..., 0] * 65536 + image[..., 1] * 256 + image[..., 2]
    position = np.searchsorted(_LUT, code).clip(0, len(_LUT) - 1)
    keys = np.where(_LUT[position] == code, _LUT_KEYS[position], -1)
    height, width = (keys.shape[0] - gap) // 2, (keys.shape[1] - gap) // 2
    return [keys[r * (height + gap): r * (height + gap) + height, c * (width + gap): c * (width + gap) + width]
            for r in range(2) for c in range(2)]


def tiles(png: bytes, gap: int = 0) -> list[np.ndarray]:
    image = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
    height, width = (image.shape[0] - gap) // 2, (image.shape[1] - gap) // 2
    return [image[r * (height + gap): r * (height + gap) + height, c * (width + gap): c * (width + gap) + width]
            for r in range(2) for c in range(2)]


def _erode(mask: np.ndarray) -> np.ndarray:
    eroded = mask.copy()
    eroded[1:] &= mask[:-1]
    eroded[:-1] &= mask[1:]
    eroded[:, 1:] &= mask[:, :-1]
    eroded[:, :-1] &= mask[:, 1:]
    return eroded


def part_stats(keys: np.ndarray, real: np.ndarray) -> dict[int, dict[str, object]]:
    found = keys >= 0
    total = int(found.sum())
    stats: dict[int, dict[str, object]] = {}
    for key in np.unique(keys[found]):
        mask = keys == key
        pixels = int(mask.sum())
        inner = _erode(mask)
        inside = int(inner.sum())
        sample = real[inner] if inside >= 4 else real[mask]
        rgb = [int(v) for v in sample.astype(np.int64).sum(axis=0) // max(1, len(sample))]
        depth, eroded = 0, inner
        while eroded.any() and depth < 64:
            depth += 1
            eroded = _erode(eroded)
        stats[int(key)] = dict(pixels=pixels, share_permille=pixels * 1000 // max(1, total),
                               inside_pct=inside * 100 // max(1, pixels), half_width_px=depth, rgb=rgb,
                               luma=(299 * rgb[0] + 587 * rgb[1] + 114 * rgb[2]) // 1000)
    return stats


def _anchor(mask: np.ndarray) -> tuple[int, int]:
    current, last = mask, mask
    while current.any():
        last, current = current, _erode(current)
    ys, xs = np.nonzero(last)
    middle = len(ys) // 2
    return int(xs[middle]), int(ys[middle])


def overlay(real: np.ndarray, keys: np.ndarray, labels: dict[int, str], scale: float = 1.0) -> Image.Image:
    image = real.copy()
    edge = np.zeros(keys.shape, bool)
    edge[:, 1:] |= (keys[:, 1:] != keys[:, :-1]) & (keys[:, 1:] >= 0) & (keys[:, :-1] >= 0)
    edge[1:] |= (keys[1:] != keys[:-1]) & (keys[1:] >= 0) & (keys[:-1] >= 0)
    edge |= np.roll(edge, 1, 0) | np.roll(edge, 1, 1)
    image[edge] = (255, 0, 160)
    out = Image.fromarray(image)
    if scale != 1.0:
        out = out.resize((int(out.width * scale), int(out.height * scale)), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(out)
    font = ImageFont.load_default(size=max(12, int(18 * scale)))
    for key, text in labels.items():
        mask = keys == key
        if mask.sum() < 30:
            continue
        x, y = _anchor(mask)
        x, y = int(x * scale), int(y * scale)
        box = draw.textbbox((x, y), text, font=font, anchor="mm")
        draw.rectangle((box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2), fill=(255, 255, 255), outline=(255, 0, 160))
        draw.text((x, y), text, fill=(0, 0, 0), font=font, anchor="mm")
    return out


def crop_box(keys: list[np.ndarray], margin: int = 24) -> tuple[int, int, int, int]:
    found = np.zeros(keys[0].shape, bool)
    for tile in keys:
        found |= tile >= 0
    ys, xs = np.nonzero(found)
    if not len(ys):
        return 0, 0, keys[0].shape[1], keys[0].shape[0]
    cx, cy = (xs.min() + xs.max()) // 2, (ys.min() + ys.max()) // 2
    side = min(int(max(xs.max() - xs.min(), ys.max() - ys.min())) + 2 * margin, keys[0].shape[0], keys[0].shape[1])
    left = int(min(max(cx - side // 2, 0), keys[0].shape[1] - side))
    top = int(min(max(cy - side // 2, 0), keys[0].shape[0] - side))
    return left, top, left + side, top + side
