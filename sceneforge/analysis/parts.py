from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image
from witness_client import RenderOptions

from sceneforge.analysis.idpass import crop_box, decode, id_module, overlay, part_stats, tag_sites, tiles
from sceneforge.clients.render import Renderer
from sceneforge.schema.config import LintConfig

GRID_GAP = 5


@dataclass
class PartAnalysis:
    lines: dict[int, int]
    front_real: list[np.ndarray]
    grid_real: list[np.ndarray]
    front_keys: list[np.ndarray]
    front_keys_solid: list[np.ndarray]
    grid_keys: list[np.ndarray]
    front_stats: list[dict] = field(default_factory=list)
    albedo_stats: list[dict] = field(default_factory=list)
    grid_stats: list[dict] = field(default_factory=list)

    def name(self, key: int) -> str:
        return f"#{key} (line {self.lines[key]})" if key in self.lines else f"#{key}"


async def analyze(renderer: Renderer, raw: str, wrapper: str, front_png: bytes, grid_png: bytes,
                  front: RenderOptions, grid: RenderOptions) -> PartAnalysis:
    tagged = tag_sites(raw)
    source, lines, mode = (raw, {}, "mesh") if tagged is None else (tagged[0], tagged[1], "site")
    see_through = await renderer.render(id_module(source, wrapper, mode, see_through=True), front)
    solid = await renderer.render(id_module(source, wrapper, mode), front)
    grid_ids = await renderer.render(id_module(source, wrapper, mode), grid)
    albedo = await renderer.render(id_module(source, wrapper, "albedo", see_through=True), front)
    analysis = PartAnalysis(lines, tiles(front_png), tiles(grid_png, GRID_GAP), decode(see_through), decode(solid),
                            decode(grid_ids, GRID_GAP))
    analysis.front_stats = [part_stats(keys, real) for keys, real in zip(analysis.front_keys, analysis.front_real,
                                                                         strict=True)]
    analysis.albedo_stats = [part_stats(keys, real) for keys, real in zip(analysis.front_keys, tiles(albedo),
                                                                          strict=True)]
    analysis.grid_stats = [part_stats(keys, real) for keys, real in zip(analysis.grid_keys, analysis.grid_real,
                                                                        strict=True)]
    return analysis


def _hex(rgb: list[int]) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def table(analysis: PartAnalysis) -> str:
    keys = sorted({key for stats in analysis.front_stats + analysis.grid_stats for key in stats} - {0})
    rows = ["part | share of the object, permille (left 30 / right 30 / below / above) | rendered color | "
            "darkest grid luma | half width px"]
    for key in keys:
        shares = [stats.get(key, {}).get("share_permille", 0) for stats in analysis.front_stats]
        widest = analysis.front_stats[max(range(4), key=lambda i: shares[i])].get(key)
        grid = [stats[key]["luma"] for stats in analysis.grid_stats if key in stats]
        rows.append(f"{analysis.name(key)} | {'/'.join(str(share) for share in shares)} | "
                    f"{_hex(widest['rgb']) if widest else '-'} | {min(grid) if grid else '-'} | "
                    f"{widest['half_width_px'] if widest else 0}")
    return "\n".join(rows)


def lints(analysis: PartAnalysis, config: LintConfig) -> list[str]:
    findings: list[str] = []
    keys = sorted({key for stats in analysis.front_stats + analysis.grid_stats for key in stats} - {0})
    for key in keys:
        name = analysis.name(key)
        shares = [stats.get(key, {}).get("share_permille", 0) for stats in analysis.front_stats]
        biggest = max(range(4), key=lambda i: shares[i])
        seen, asked = analysis.front_stats[biggest].get(key), analysis.albedo_stats[biggest].get(key)
        solid = sum(int((view == key).sum()) for view in analysis.front_keys_solid)
        through = sum(int((view == key).sum()) for view in analysis.front_keys)
        grid = [stats[key]["luma"] for stats in analysis.grid_stats if key in stats]
        if seen and asked:
            shift = seen["luma"] - asked["luma"]
            if (config.dark_shift is not None and shift <= -config.dark_shift
                    and max(shares) >= config.dark_min_permille):
                findings.append(f"{name} renders {_hex(seen['rgb'])} although its color is {_hex(asked['rgb'])}: "
                                f"the light, metalness or roughness darken it")
            elif config.light_shift is not None and shift >= config.light_shift:
                findings.append(f"{name} renders much lighter than its color ({_hex(seen['rgb'])} vs "
                                f"{_hex(asked['rgb'])})" + (", seen through glass" if 2 * solid < through else ""))
        if max(shares) == 0:
            if solid == 0 and config.invisible:
                findings.append(f"{name} is not visible from any of the judge's four front cameras"
                                + (" (only in the grid)" if grid else ""))
            continue
        if config.tiny_permille is not None and max(shares) < config.tiny_permille:
            findings.append(f"{name} is under {config.tiny_permille / 10:g}% of the object in every front view")
        if (config.hairline_px is not None and seen and seen["pixels"] >= 40
                and seen["half_width_px"] <= config.hairline_px):
            findings.append(f"{name} renders as a hairline at the judge's 1024 px")
        if config.black_grid_luma is not None and grid and min(grid) < config.black_grid_luma:
            findings.append(f"{name} is near-black on the gray grid (luma {min(grid)})")
        if config.near_white_luma is not None and seen and seen["luma"] > config.near_white_luma:
            findings.append(f"{name} is near-white ({_hex(seen['rgb'])}); its edge may vanish on white")
    findings += _neighbours(analysis, config)
    return findings


def _neighbours(analysis: PartAnalysis, config: LintConfig) -> list[str]:
    keys, stats = analysis.front_keys[0], analysis.front_stats[0]
    pairs: dict[tuple[int, int], int] = {}
    for one, two in ((keys[1:], keys[:-1]), (keys[:, 1:], keys[:, :-1])):
        touching = (one != two) & (one > 0) & (two > 0)
        for x, y in zip(one[touching].tolist(), two[touching].tolist(), strict=True):
            pair = (min(x, y), max(x, y))
            pairs[pair] = pairs.get(pair, 0) + 1
    findings = []
    for (x, y), count in sorted(pairs.items()):
        smaller = min(int((keys == x).sum()), int((keys == y).sum()))
        if config.zfight_share is not None and smaller >= 400 and count >= config.zfight_share * smaller:
            findings.append(f"{analysis.name(x)} and {analysis.name(y)} overlap at the same depth (z-fighting): the "
                            f"surface flickers between them")
        elif (config.same_color_l1 is not None and x in stats and y in stats
              and sum(abs(p - q) for p, q in zip(stats[x]["rgb"], stats[y]["rgb"], strict=True))
              < config.same_color_l1):
            findings.append(f"{analysis.name(x)} and {analysis.name(y)} render almost the same color "
                            f"({_hex(stats[x]['rgb'])}): the boundary between them is invisible")
    return findings


def sheet(analysis: PartAnalysis, tile: int = 512) -> Image.Image:
    left, top, right, bottom = crop_box(analysis.front_keys_solid)
    labels = {key: str(key) for stats in analysis.front_stats for key in stats if key > 0}
    out = Image.new("RGB", (2 * tile + 6, 2 * tile + 6), (200, 200, 200))
    for index in range(4):
        keys = analysis.front_keys[index][top:bottom, left:right]
        real = analysis.front_real[index][top:bottom, left:right]
        view = overlay(real, keys, labels, scale=tile / keys.shape[0])
        out.paste(view.resize((tile, tile)), ((index % 2) * (tile + 6), (index // 2) * (tile + 6)))
    return out
