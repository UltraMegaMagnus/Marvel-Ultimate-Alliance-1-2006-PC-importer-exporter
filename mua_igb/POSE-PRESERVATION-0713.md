# 0.7.13: preserve Actors bind pose

Static Actors export now preserves authored mesh positions and bone bind frames instead of rebaking them into the reference T-pose. This is the same export path used for the matched Max original-pose test skin that the user confirmed works in game, including converted animations. The 0.7.12 Bip01/pelvis pivot alignment and weighted-root protection remain active. No original Blender meshes, rest poses or actions are modified.

This preserves the rest/bind pose; it does not bake whichever animation frame happens to be visible into a static skin. Use Export Animation for embedded animated models. Existing embedded animation, animation import/export modes, textures and registration behavior are unchanged. Keep Compensate Bip01 Height WIP off for the standard root correction.

For an original-pose result, export your original blend or reimport the original skin: a skin already rebaked by older versions does not contain its pre-rebake pose. The earlier automatic T-pose workflow is superseded by this release. Animation import still retains its existing Support for all skeletons fitting; this release changes static Actors output only.

The supplied Max pair established that reference-pose preparation caused the pose change. A separate roundtrip retained positions within 0.0000043 file units and calculated animation deformation within 0.000059 units of the known-working Max skin. The user verified that test IGB in game. Automated checks for this packaged release are recorded separately in the workspace validation logs. These checks cannot guarantee every arbitrary rig or game animation.

## Packaged release checks

Blender 4.5.3 passed: original Max IGB roundtrip, direct export of the newly supplied Max .blend, position/weight correspondence, light3/idle/Max-animation skinning comparisons, bad-skin root-gap repair, repeated export, unchanged original Blender geometry/rest matrices, synthetic parent/height/weighted-root safeguards, Wii texture recovery, environment textures, blended embedded model animation, fresh ZIP installation and enabled-addon upgrade from 0.7.12. Maximum matched-vertex animation deviation for the blend export was 0.000061 file units. Blender reported the blend came from a newer 4.5 build; the source blend was not saved or modified. No regressions were detected in these checks.

Only blender_export.py and the version declaration in __init__.py changed among Python modules relative to 0.7.12. The 0.7.12 ZIP is preserved. Automated results accompany this package under validation/0713_*.json.
