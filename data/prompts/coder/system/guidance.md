## How to build it

- Reconstruct only the main object; ignore background, ground, shadows and reflections.
- Match the silhouette and proportions first (estimate each part relative to the largest dimension), then
  reproduce counts of repeated features exactly (legs, spokes, ribs, buttons, numerals, windows...).
- Be thorough: a complete module is usually 6,000 to 8,000 tokens of code. Spend that on what the photo shows: every
  distinct part, accurate profiles, bevels, rims, seams, handles, holes, feet, and printed patterns or labels (a small
  DataTexture on the right face). Use helper functions and loops, so the length goes into detail and not repetition.
- Copy the details a viewer would name first: visible text and logos letter by letter, rows of stones, chain links,
  foot caps, zippers, mesh and weave, and the colors of parts seen through windows.
- Match colors from the image as hex values, keeping their hue and saturation; do not wash them out. Between two
  shades, take the deeper one in the bright light, but avoid near-black: use at least about #2a2a2a for black parts.
- Metals: metalness about 0.5-0.6, low roughness and a light base color (silver around #c0c0c0, gold around #d4a64a).
  Silver at metalness 1.0 is about a tenth darker than at 0.6.
- Glass and clear plastic: transparent: true with opacity 0.35-0.6, a visible tint and visible rims, so the object
  stays visible on a white background.
- Use smooth, well-tessellated forms for organic shapes (LatheGeometry profiles, scaled spheres, CapsuleGeometry,
  TubeGeometry along CatmullRomCurve3, ExtrudeGeometry with bevels) and crisp primitives for hard surfaces. Lathe and
  Shape points are THREE.Vector2; tube paths use THREE.Vector3.
- Openings (cups, bowls, pots, tubes) need real wall thickness: a lathe profile that walks up the outside, across the
  rim and back down the inside, or Shape holes for extrusions. A dark patch is not a hole.
- Thin parts (wires, rods, frames, straps) need a visible thickness of at least about 1% of the object's size.
- Wheels turn with their axle: on a vehicle shown from the side (axles along Z), tori keep their default orientation
  and cylinders need rotation.x = Math.PI / 2; on a vehicle facing +Z (axles along X), tori need
  rotation.y = Math.PI / 2 and cylinders rotation.z = Math.PI / 2.
- Slow scenes time out. Stay under about 60,000 vertices, 120 draw calls and 5,000 instances; share geometries and
  materials, use InstancedMesh for repeats, and keep each DataTexture at most 512x512. Geometry with a map needs UVs.
- Never put a unary minus or plus directly before ** (-x ** 2 is a syntax error); write -(x ** 2).
- Fully deterministic code only.
