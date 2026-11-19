from __future__ import annotations

import io
from collections.abc import Awaitable, Callable

import numpy as np
from PIL import Image, ImageFilter

from sceneforge.pipeline.wrapper import TILT_LINE, tilted

SIZE = 256
NORM = 128
TILTS = (0, 8, 15, 22)
MIN_GAIN = 0.001
USED = 22


def _rgb(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB").resize((SIZE, SIZE), Image.Resampling.BILINEAR)
                      ).astype(np.float32)


def _fit(xs: np.ndarray, ys: np.ndarray, values: np.ndarray) -> list[tuple[float, float, float]]:
    count = len(xs)
    sx, sy = int(xs.sum()), int(ys.sum())
    sxx, sxy, syy = int((xs * xs).sum()), int((xs * ys).sum()), int((ys * ys).sum())
    det = sxx * (syy * count - sy * sy) - sxy * (sxy * count - sy * sx) + sx * (sxy * sy - syy * sx)
    planes = []
    for channel in range(values.shape[1]):
        value = values[:, channel]
        sv, sxv, syv = int(value.sum()), int((xs * value).sum()), int((ys * value).sum())
        if det == 0:
            planes.append((0.0, 0.0, sv / count if count else 0.0))
            continue
        p = sxv * (syy * count - sy * sy) - sxy * (syv * count - sy * sv) + sx * (syv * sy - syy * sv)
        q = sxx * (syv * count - sy * sv) - sxv * (sxy * count - sy * sx) + sx * (sxy * sv - syv * sx)
        r = sxx * (syy * sv - sy * syv) - sxy * (sxy * sv - syv * sx) + sxv * (sxy * sy - syy * sx)
        planes.append((p / det, q / det, r / det))
    return planes


def _shade(planes: list[tuple[float, float, float]], xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    return np.stack([xs * p + ys * q + r for p, q, r in planes], axis=-1)


def _plane(rgb: np.ndarray) -> tuple[np.ndarray, float, np.ndarray]:
    height, width = rgb.shape[:2]
    ys, xs = np.mgrid[0:height, 0:width]
    edge = 8
    border = np.zeros((height, width), bool)
    border[:edge] = border[-edge:] = True
    border[:, :edge] = border[:, -edge:] = True
    border_xs, border_ys = xs[border].astype(np.int64), ys[border].astype(np.int64)
    values = rgb[border].astype(np.int64)
    keep = np.ones(len(values), bool)
    for _ in range(3):
        planes = _fit(border_xs[keep], border_ys[keep], values[keep])
        errors = np.abs(_shade(planes, border_xs, border_ys) - values).max(1)
        keep = errors <= max(6.0, 2.5 * float(np.median(errors)))
    background = _shade(planes, xs, ys)
    return background, float(np.median(errors[keep])) if keep.any() else 0.0, border


def _morph(mask: np.ndarray, grow: int, shrink: int) -> np.ndarray:
    image = Image.fromarray(mask.astype(np.uint8) * 255)
    if grow:
        image = image.filter(ImageFilter.MaxFilter(2 * grow + 1))
    if shrink:
        image = image.filter(ImageFilter.MinFilter(2 * shrink + 1))
    return np.asarray(image) > 127


def _fill(mask: np.ndarray) -> np.ndarray:
    open_ = np.pad(~mask, 1, constant_values=True)
    seen = np.zeros_like(open_)
    seen[0, 0] = True
    while True:
        grown = seen.copy()
        grown[1:] |= seen[:-1]
        grown[:-1] |= seen[1:]
        grown[:, 1:] |= seen[:, :-1]
        grown[:, :-1] |= seen[:, 1:]
        grown &= open_
        if (grown == seen).all():
            break
        seen = grown
    return ~seen[1:-1, 1:-1]


def _segment(rgb: np.ndarray, background: np.ndarray, noise: float, border: np.ndarray,
             scale: float) -> np.ndarray | None:
    distance = np.abs(rgb - background).max(-1)
    cut = scale * max(18.0, 3 * noise + 8)
    light, background_light = rgb.mean(-1), background.mean(-1)
    tint = np.abs((rgb - light[..., None]) - (background - background_light[..., None])).max(-1)
    shadow = (light < .98 * background_light) & (light > .45 * background_light) & (tint < 12) & (distance < 4 * cut)
    mask = _fill(_morph(_morph((distance > cut) & ~shadow, 0, 1), 4, 3))
    if mask.sum() < 80 or mask[border].mean() > .5:
        return None
    return mask


def _norm(mask: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(mask)
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    height, width = crop.shape
    scale = NORM / max(height, width)
    height2, width2 = max(1, round(height * scale)), max(1, round(width * scale))
    small = np.asarray(Image.fromarray(crop.astype(np.uint8) * 255).resize((width2, height2),
                                                                           Image.Resampling.BILINEAR)) > 127
    out = np.zeros((NORM, NORM), bool)
    top, left = (NORM - height2) // 2, (NORM - width2) // 2
    out[top:top + height2, left:left + width2] = small
    return out


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    a, b = _norm(a), _norm(b)
    return float((a & b).sum() / max(1, (a | b).sum()))


def photo_mask(image: bytes) -> np.ndarray | None:
    rgb = _rgb(image)
    background, noise, border = _plane(rgb)
    mask = _segment(rgb, background, noise, border, 1.0)
    if mask is None:
        return None
    for scale in (.75, 1.25):
        other = _segment(rgb, background, noise, border, scale)
        if other is None or _iou(mask, other) < .8:
            return None
    return mask


def _square(mask: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(mask)
    box = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    height, width = box.shape
    side = max(height, width)
    out = np.zeros((side, side), dtype=bool)
    top, left = (side - height) // 2, (side - width) // 2
    out[top:top + height, left:left + width] = box
    return np.asarray(Image.fromarray(out.astype(np.uint8) * 255).resize((128, 128))) > 127


async def best_tilt(image: bytes, source: str, render: Callable[[str], Awaitable[bytes]]) -> int | None:
    if source.count(TILT_LINE) != 1:
        return None
    photo = photo_mask(image)
    if photo is None:
        return None
    target = _square(photo)
    scores: dict[int, float] = {}
    for angle in TILTS:
        png = await render(tilted(source, angle))
        rgb = np.asarray(Image.open(io.BytesIO(png)).convert("RGB").resize((SIZE, SIZE))).astype(np.float32)
        shown = (255 - rgb).max(-1) > 8
        if shown.sum() <= 20:
            return None
        square = _square(shown)
        scores[angle] = float((target & square).sum() / max((target | square).sum(), 1))
    best = max(TILTS, key=lambda angle: (scores[angle], -angle))
    return best if best == USED and scores[best] - scores[0] >= MIN_GAIN else None
