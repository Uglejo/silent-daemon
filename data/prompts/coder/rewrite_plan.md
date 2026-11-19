Improve the current module so it matches the target more closely. Every visible difference costs points.
The images above are the target, then the current module close up from the judge's four front cameras in this order:
30 degrees left, 30 degrees right, from slightly below, from above. Each close-up shows the module at the judge's
resolution, so judge its fine detail from these. The last image is the judge's second-stage grid: bright neutral light on
gray, with views around the object.

Compare them with the target and fix what differs, in this order:
1. Pose: which side faces the cameras; upright, lying, leaning or diagonal; the same left-right direction. A wrong pose
   costs the most, so fix it first by turning the whole object in code.
2. Proportions: the overall width : height : depth, then the size and position of each part.
3. Signature details: what a viewer would name first about this object (exact text letter by letter, rows of stones,
   chain links, foot caps, seat colors, zippers, mesh, handles). Count every repeated part.
4. Colors and materials in the bright views: hue, saturation, depth of tone, gloss, transparency.
5. The grid: every part must stay recognizable and keep its color. Lighten or lower the metalness of any part that
   goes black there, and complete any back or side that is missing, flat or hollow.

Ignore differences caused only by the plain background or the lights. Keep everything that already matches; do not
simplify or drop correct detail. Make each change large enough to be visible in the renders.{{focus}}{{rejected}}

A reviewer compared the renders with the target and wrote the edit plan below, most damaging difference first. Part
numbers (#N, line L) refer to the geometry built on source line L of the current module. Apply every edit unless the
target clearly contradicts it, then fix anything else you see:
{{plan}}

Reply format for this step only: first the differences you found, most damaging first (at most 12 short bullets, no
code), then exactly one ```javascript fenced code block with the complete improved module, not a patch.

Current module:
{{source}}
