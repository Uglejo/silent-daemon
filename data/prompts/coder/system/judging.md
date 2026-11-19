## How the asset is judged

- Your module is wrapped automatically: the returned object is centered and uniformly scaled to fill the
  [-0.5, 0.5] cube. Build at any convenient natural scale; do not add your own fitting code.
- First stage, deciding most comparisons: a VLM judge compares four views by cameras on the +Z side with the target, on
  a white background in bright neutral light: 30 degrees left of +Z, 30 degrees right of +Z, straight on from slightly
  below, and straight on from 30 degrees above. Penalties: 3 = minor shape differences or missing small details,
  5 = wrong style or significant details missing, 7 = related but wrong object, 10 = wrong object.
- Second stage, deciding close comparisons: the judge rates a 2x2 grid of views around the object (front-right, back
  right, back left, front-left, from slightly above) on a mid-gray background in the same bright neutral light. It
  checks color and material accuracy, missing details, and a checklist of countable features, so every part must stay
  recognizable and keep its color there too.
- A missing, flat, hollow or paper-thin back or side loses. Stray floors, walls, backdrops, pedestals or boxes count as
  artifacts. Near-white thin geometry is checked against a gray background.

## Pose

Y is up. Place the object so the +Z cameras see it the way the photo does: the same side toward the viewer, the same
upright, lying or leaning pose, the same left-right direction. Do not turn it into a canonical pose: an object that
lies, leans or runs diagonally in the photo keeps that lean or diagonal in the X-Y plane.
- A long object shown from the side (car, boat, train, shoe, knife, animal in profile) runs along X, not toward the
  camera.
- A flat object shown face-on (plate, tray, clock, sign, coin, book cover) turns that face toward +Z, so the cameras
  see the face and not a thin edge.
