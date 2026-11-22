> Historical release notes: fixed-height preparation below is superseded by ROOT-PIVOT-0712.md.

# 0.7.11: exported actor pivot and animated FX compatibility

## Recommended workflow with existing game animations

Re-export the affected skin as Actors, with Export Animation off for a static actor skin and Compensate Bip01 Height WIP off. The exporter automatically normalizes the detached Bip01 bind pivot's Z to 41.819530487 file units, the height in the supplied vanilla PC skin. This is not a character/mesh scale. After the existing actor-axis preparation, child world bind positions and vertex arrays remain unchanged; the skeleton writer rebuilds the root-to-child translations and inverse matrices. Your original Blender mesh, rig, rest pose and actions are not modified.

This removes dependence on the custom Bip01 bind height when ordinary absolute-root animations and rest-relative pelvis tracks are combined. It does not require replacing every existing idle/attack clip. Literal animation export remains the default. Live Bip01 translation, including vertical bounce, remains active. A constant child translation equal to its bind offset is omitted as in the Max exporter, so it does not override the receiving skin's corrected offset. Non-rest/animated child translations are retained; RemovePos stays off by default.

Normalization requires Bip01 -> Bip01 Pelvis and an unweighted Bip01 control pivot. Identity parent helpers such as the supplied custom rig's bone0 are supported. Nonidentity parents and vertex-weighted Bip01 produce a warning and retain their height: changing these pivots automatically could damage deformation. Embedded animated models and Models/Mannequins do not get this static-actor normalization. Explicit legacy Compensate Height bypasses it.

## Optional animation-side alternative

`Adapt Bip01 height to skin (whole animation set)` is OFF by default. It transfers Bip01 displacement into child translations through the inverse animated root rotation/scale, leaving the skin to supply Bip01's constant position. This representation was observed in the supplied Max export. It preserves descendant poses on the authoring rig, but must be used consistently across the whole animation set, including idle. Do not enable it merely to replace one attack among unchanged clips. It skips weighted Bip01 and Motion parented beneath Bip01. Motion metadata is preserved. For external ExtractMotionRaven processing, keep this option off, or use the add-on's Extract Motion before adaptation. Support-all import retains active pelvis/root-child translation curves.

## FX changes

Transforms use their explicit serialized target (9..16 for texture matrices), rather than the inherited texture-matrix enable state. This restores the nested model-view transform in fx_d_handfountain. UV export has explicit flags/target, complete translation/rotation/scale/time arrays, valid descendant bounds, consistent nanosecond timestamps, and scene end time. Geometry and rigid-animation wrappers receive bounds. UV sampling follows the selected scene range/rate and playback setting.

## Validation and limits

Blender 4.5.3: static actor pivot tests at 25, 41.819530487, 55 and 70 file units; detached custom-skin export with unchanged Blender rig/vertices; unchanged light3/idle root-height calculations; live default Bip01 keys and masked resting pelvis. The optional animation-side alternative was compared with literal exports for light3, idle, the Max sample and all 34 Spider-Man combat actions on vanilla/custom rigs (68 comparisons), plus rotating/scaling roots and fallback cases. Both FX samples passed both model modes and all four playback modes at 12 Hz, sequence/bounds/duration checks and reimport. Packaged tests cover Wii/materials, blended embedded animation and fresh installation/upgrade.

game.exe and the supplied libIGSg.dll were inspected. Native binding selects source translation or the destination skeleton's constant translation via the animation mask. These are structural, mathematical and Blender tests, not a gameplay run. In-game transition behaviour and whether the reported FX crashes are fully resolved still require confirmation. The exact exported animation shown in the video has not yet been identified; tests use the supplied source clips. No guarantee is made for arbitrary weighted-root rigs, nonstandard axes/hierarchies, or animations which explicitly override the pelvis bind offset.
