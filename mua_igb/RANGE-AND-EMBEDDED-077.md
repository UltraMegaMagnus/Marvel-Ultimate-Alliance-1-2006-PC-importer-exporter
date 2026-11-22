UPDATE: the blended-animation rejection described in this older release note is removed in 0.7.8. See BLENDED-ROOT-TEXTURES-078.md.

# 0.7.7 — scene range and embedded model animation

Support for Vanilla skeleton bypasses fitted-bind retargeting and keeps source rotation, translation (including limbs), and scale channels. Tested with the supplied attack animation plus a fixture containing animated root/limb translations and nonuniform scales. As with any Blender conversion, this means transform fidelity within floating-point/sampling precision, not byte-identical files.

Animation export now uses Scene Start and End, not Action frame_range. The start is written as time zero. Single-frame ranges export one sample. Embedded node animation and UV animation also use the scene range. Force Sample uses exactly the requested number of intervals, with default 1 (two endpoint samples); leave Force Sample off to retain ordinary sampling detail.

RemovePos is now named RemovePos WIP and is OFF by default. Compensate Bip01 Height is now Compensate Bip01 Height WIP, retaining its OFF default. These changes do not silently override explicit saved operator settings.

Imported embedded node-animation rigs are marked for animated-model export. With Export Animation on, rigid animated models now write real igTransformSequence1_5 scene nodes, in both Actors and Models/Mannequins mode, rather than a static skin. Translation, rotation and scale are sampled from the evaluated rig; the original mesh and rig are not modified. Static bind scale is accounted for when importing embedded scale keys. Meshes containing independent rigid pieces can be split by bone for output. Truly blended vertex weights or triangles spanning different bones are rejected explicitly in this node-animation path; they are not silently flattened. This release does not implement a general embedded skinned-actor animation player.

shuriken.igb was imported, exported and reimported in Blender 4.5.3; nine sampled poses agreed within 0.00000012 metres maximum nearest-vertex error. The output contains an attached live transform source and all transform channels. Tests also cover Vanilla translation/rotation/nonuniform scale, scene-range trimming, one-frame ranges, and Force Sample=1. The game was not launched, so in-game playback remains to be confirmed.

For an existing blend imported before this version, reimport the animated prop to acquire the embedded-animation marker and scale handling. Set the scene frame range to the interval you want exported and leave Export Animation enabled.
