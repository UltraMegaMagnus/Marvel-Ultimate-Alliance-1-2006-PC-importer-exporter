# 0.7.12: actor root/pelvis bind offset

The supplied working Max2012 skin has coincident Bip01 and Bip01 Pelvis pivots (world Z 41.40886). The failing Blender skin instead has Bip01 Z 41.81953 and pelvis Z 38.03511: its local pelvis translation is -3.78442. This disproves the previous fixed-height assumption. When a clip switches between skeleton-provided pelvis translation and an explicit zero translation, that gap becomes a discontinuity.

Static Actors export now aligns the unweighted Bip01 control pivot with its actual pelvis after actor-axis preparation. It recomputes local skeleton translations and inverse bind matrices. It preserves all child world bind positions and mesh vertices during this pivot correction, and never changes the original Blender mesh, rig or actions. There is no fixed world height. Transformed helper parents are supported. Re-export affected skins as Actors with Export Animation and Compensate Bip01 Height WIP off.

Animation export remains unchanged: live Bip01 translations are retained; RemovePos WIP and whole-set Adapt Bip01 height remain off by default. Do not enable whole-set adaptation for a single replacement attack among unchanged clips. Embedded animated models retain their existing animation/bind handling.

Safety: automatic pivot correction requires the named direct Bip01 -> Bip01 Pelvis pair and an unweighted Bip01. A weighted Bip01 retains its pivot with a warning because moving it changes deformation. This does not make arbitrary bone hierarchies or incompatible animation axes universally interchangeable.

Blender 4.5.3 validation: both supplied skins import/export/reimport/re-export with zero local pelvis translation; original Blender bone matrices and vertices are unchanged. Maximum repeated-export joint drift was 0.000012 file units. Synthetic tests cover four character heights, translated/rotated helper parents, rotating roots, unchanged child positions/vertices and weighted-root protection. Results are in validation/0712_results.json.

The supplied failing file contains static defaultAnimation scaffolding, not the attack clip from the video. The verified gap explains a concrete transition mismatch; actual gameplay and every possible transition are not verified. The earlier native-code inspection supports translation-mask selection of source versus skeleton translation, but is not a gameplay test.
