from __future__ import annotations

import re

from sceneforge.analysis.jslex import exponent_safe
from sceneforge.pipeline.assets import wrapper_part

WRAPPER = wrapper_part("wrapper.js")
TILT = wrapper_part("tilt.js")
FIT = wrapper_part("fit.js")
GLOW_FUNCTION = wrapper_part("glow.js")
BAKE_FUNCTION = wrapper_part("bake.js")
GRADE_FUNCTION = wrapper_part("grade.js")
OUTLINE_TILT = wrapper_part("outline_tilt.js")
METAL_PASS = wrapper_part("metal.js")
EXPORT_LINE = "export default function generate(THREE) {\n"
TILT_LINE = "if (flat.y < 0.5 * Math.max(flat.x, flat.z)) root.rotation.x = 15 * Math.PI / 180;"
ASSET_CALL = "root.add(__asset404(THREE));"

_EXPORT = re.compile(r"export\s+default\s+function\s*(?:[A-Za-z_$][\w$]*\s*)?\(")
_FENCED = re.compile(r"```(?:javascript|js|mjs)?[^\n]*\n([\s\S]*?)```")
_METAL = re.compile(r"metalness\s*[:=]\s*(0?\.(2[5-9]|[3-9])|1)")


def chain_wrapper(fill: float, glow: float = 0.0, outline_fit: bool = False, flat_tilt: bool = True) -> str:
    wrapper = WRAPPER.replace(FIT, TILT + FIT, 1).replace("0.95 / extent", f"{fill} / extent", 1)
    if outline_fit:
        wrapper = wrapper.replace(TILT, OUTLINE_TILT, 1).replace(
            EXPORT_LINE, BAKE_FUNCTION + GRADE_FUNCTION + "\n" + EXPORT_LINE, 1)
    if glow:
        wrapper = wrapper.replace(TILT, f"  __lift404(root, {glow});\n" + TILT, 1)
        wrapper = wrapper.replace(EXPORT_LINE, GLOW_FUNCTION + "\n" + EXPORT_LINE, 1)
    if not flat_tilt:
        wrapper = wrapper.replace(TILT, "", 1)
    return wrapper


def extract_source(text: str) -> str | None:
    text = text.strip()
    blocks = [block for block in _FENCED.findall(text) if _EXPORT.search(block)]
    if blocks:
        text = blocks[-1].strip()
    return exponent_safe(text) if _EXPORT.search(text) else None


def normalize(source: str, wrapper: str) -> str:
    found = _EXPORT.search(source)
    if found is None:
        return source
    return source[: found.start()] + "function __asset404(" + source[found.end():] + "\n" + wrapper


def tilted(source: str, angle: int) -> str:
    return source.replace(TILT_LINE, f"root.rotation.x = {angle} * Math.PI / 180;")


def with_metal_pass(source: str, metalness: float, roughness: float) -> str | None:
    if not _METAL.search(source):
        return None
    at = source.rfind("const box = new THREE.Box3()")
    at = at if at >= 0 else source.rfind("new THREE.Box3()")
    if at < 0:
        return None
    line = source.rfind("\n", 0, at) + 1
    metal = METAL_PASS.replace("__METALNESS__", f"{metalness:g}").replace("__ROUGHNESS__", f"{roughness:g}")
    return source[:line] + "  " + metal + "\n" + source[line:]


def turned_group(source: str, name: str, rotation: str) -> str:
    replacement = f"const {name} = new THREE.Group(); {rotation} {name}.add(__asset404(THREE)); root.add({name});"
    return source.replace(ASSET_CALL, replacement, 1)


def rotated(source: str, yaw: int) -> str:
    if yaw == 0:
        return source
    return turned_group(source, "__yaw404", f"__yaw404.rotation.y = {yaw} * Math.PI / 180;")


def stood_up(source: str, pitch: int) -> str:
    return turned_group(source, "__pose404", f"__pose404.rotation.x = {pitch} * Math.PI / 180;")


def turned(source: str, yaw: int, pitch: int, roll: int) -> str:
    if not (yaw or pitch or roll):
        return source
    return turned_group(source, "__turn404",
                        "__turn404.rotation.order = 'YXZ'; "
                        f"__turn404.rotation.set({pitch} * Math.PI / 180, {yaw} * Math.PI / 180, "
                        f"{roll} * Math.PI / 180);")


def turned_module(raw: str, yaw: int, pitch: int, roll: int) -> str:
    found = _EXPORT.search(raw)
    if found is None:
        return raw
    inner = raw[: found.start()] + "function __posedObject(" + raw[found.end():]
    return inner + f"""

// The pose was checked against the target: keep this group unless the target clearly shows otherwise.
export default function generate(THREE) {{
  const pose = new THREE.Group();
  pose.rotation.order = 'YXZ';
  pose.rotation.set({pitch} * Math.PI / 180, {yaw} * Math.PI / 180, {roll} * Math.PI / 180);
  pose.add(__posedObject(THREE));
  return pose;
}}
"""
