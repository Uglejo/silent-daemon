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

Reply format for this step only: first the differences you found, most damaging first (at most 12 short bullets, no
code), then your changes as edits of the current module, not as a new module. Each edit is one block:
<<<<<<< SEARCH
lines copied from the current module
=======
the lines that take their place
>>>>>>> REPLACE
The SEARCH lines must match the current module character for character, indentation included, and be long enough to
occur in it only once. The blocks are applied in the order you give them. A block with nothing between its ======= and
>>>>>>> REPLACE lines deletes the lines it searched for. To insert code, search for the line just before the insertion
and repeat that line at the start of the replacement. When most of the module has to change (the whole pose, the
overall proportions), reply with the complete module in exactly one ```javascript fenced code block and no edit blocks.

Current module:
{{source}}
