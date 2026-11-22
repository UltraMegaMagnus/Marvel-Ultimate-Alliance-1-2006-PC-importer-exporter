# Actors export 0.7.4

Export Actors normally. Preparation now happens automatically on detached export arrays, not on Blender objects. No reference-file picker or manual preparation is required. The bundled MUA bind reference supplies joint orientations and directions. Custom parent-to-child distances, additional bones, weights, UVs and topology are retained. Positions and normals are transformed together with the bind skeleton. This applies to every Actors export, not a particular blend filename.

Original mesh/armature data, pose, selection and scene time are not converted. Models/Mannequins are unchanged. Evaluated shape keys/modifiers follow the existing exporter behavior; their source data is not modified.

Matching MUA bone names are required for existing game animations. Entirely unrelated bone names cannot be mapped automatically and produce a warning while retaining their custom bind pose. Custom animations authored for a different bind convention are not retargeted by this model-export change.

Material deduplication on name-based mesh merge remaps polygon material indices and preserves distinct shader settings. Alchemy texture properties automatically create/remove the corresponding real image nodes; empty shader initialization also removes unused image nodes. No placeholder images are generated.

Validation: Blender 4.5.3 LTS, original wasted.blend exported repeatedly without mesh/rig/pose/selection/time changes; exported skin reimported and checked at 108 poses from 36 installed Luke Cage actions. Separate CPU skinning and Blender evaluation differed by less than 0.000008 metres. Automatic geometry matched the previous reference preparation within 0.0762 mm (weight/conversion rounding). Synthetic custom proportions and additional bones passed distance, topology, weights, UV, normal and repeated-preparation checks. Material deduplication and all six texture property add/remove controls passed. The game was not launched; these are Blender/file-level checks, not in-game confirmation.
