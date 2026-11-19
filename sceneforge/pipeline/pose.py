from __future__ import annotations

import io

import numpy as np
from PIL import Image
from witness_client import RenderOptions

from sceneforge.clients.render import Renderer
from sceneforge.pipeline.wrapper import rotated

YAWS = (0, 90, 180, 270)
MASK_SIZE = 64
FRONT_THETAS = (30.0, 330.0, 0.0, 0.0)
FRONT_PHIS = (0.0, 0.0, 15.0, -30.0)
KEY = (255, 0, 255)
MASKS = RenderOptions(thetas=FRONT_THETAS, phis=FRONT_PHIS, img_size=128, gap=0, bg_color="ff00ff", lighting="neutral")
FLAT = RenderOptions(thetas=(0.0, 90.0), phis=(0.0, 0.0), img_size=128, gap=0, bg_color="ff00ff", lighting="neutral")


def _normalized(mask: np.ndarray) -> np.ndarray | None:
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    crop = mask[rows[0]: rows[-1] + 1, cols[0]: cols[-1] + 1]
    height, width = crop.shape
    side = max(height, width)
    square = np.zeros((side, side), dtype=np.uint8)
    top, left = (side - height) // 2, (side - width) // 2
    square[top: top + height, left: left + width] = crop.astype(np.uint8) * 255
    resized = Image.fromarray(square).resize((MASK_SIZE, MASK_SIZE), Image.Resampling.BILINEAR)
    return np.asarray(resized, dtype=np.float32) / 255.0


def reference_mask(image: bytes) -> np.ndarray | None:
    rgb = np.asarray(Image.open(io.BytesIO(image)).convert("RGB").resize((256, 256), Image.Resampling.BILINEAR),
                     dtype=np.float32)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    distance = np.sqrt(((rgb - np.median(border, axis=0)) ** 2).sum(axis=2))
    mask = distance > 40.0
    if mask.mean() < 0.01 or mask.mean() > 0.95:
        return None
    return _normalized(mask)


def _keyed(png: bytes) -> np.ndarray:
    grid = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.float32)
    return np.sqrt(((grid - np.array(KEY, dtype=np.float32)) ** 2).sum(axis=2)) > 60.0


async def _render_masks(renderer: Renderer, source: str) -> list[np.ndarray | None]:
    shown = _keyed(await renderer.render(source, MASKS))
    return [_normalized(shown[row * 128: (row + 1) * 128, col * 128: (col + 1) * 128])
            for row in range(2) for col in range(2)]


async def is_flat(source: str, renderer: Renderer) -> bool:
    shown = _keyed(await renderer.render(source, FLAT))
    front, side = shown[:, :128], shown[:, 128:]
    if not front.any() or not side.any():
        return False
    height = np.ptp(np.flatnonzero(front.any(axis=1))) + 1
    width = np.ptp(np.flatnonzero(front.any(axis=0))) + 1
    depth = np.ptp(np.flatnonzero(side.any(axis=0))) + 1
    return bool(height < 0.5 * max(width, depth))


async def silhouette_scores(image: bytes, source: str, renderer: Renderer) -> dict[int, float] | None:
    target = reference_mask(image)
    if target is None:
        return None
    scores: dict[int, float] = {}
    for yaw in YAWS:
        values = []
        for mask in await _render_masks(renderer, rotated(source, yaw)):
            if mask is None:
                continue
            union = np.maximum(mask, target).sum()
            values.append(float(np.minimum(mask, target).sum() / union) if union else 0.0)
        scores[yaw] = round(sum(values) / len(values), 4) if values else 0.0
    return scores


async def best_yaw(image: bytes, source: str, renderer: Renderer, margin: float = 0.04) -> int:
    scores = await silhouette_scores(image, source, renderer)
    if not scores:
        return 0
    yaw = max((90, 270), key=lambda y: (scores[y], -y))
    return yaw if scores[yaw] - scores[0] >= margin else 0
