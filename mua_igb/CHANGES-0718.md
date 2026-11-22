# 0.7.18 — shader controls and mannequin export

- Alpha remains an enable checkbox. Choose Alpha 6 (default, reference 0.5) or Alpha 4 (reference 0.0); only one function is exported. Imported values are preserved until edited.
- DepthWriteStateAttr: unchecked omits the attribute. Checked writes an explicit On or Off value, including Off. The duplicate Shader Vertex Colors control is removed; use Geometry > Vertex Colors.
- Default standalone animation export retains all sampled translations, including constant child translations, over the Blender scene frame range.
- RemovePos WIP remains off by default. It runs last after Extract Motion and any optional adaptation, stripping only the 29 bone names listed in the supplied batch. Bip01, Bip01 Pelvis, Motion, and other/custom bone names remain unchanged. The native batch optimizations and this implementation produce matching channels and values.
- Compensate Bip01 Height WIP and its mesh-shift export path are removed. Existing tested Actor bind-axis/root-pivot corrections remain.
- Separate Actors, Model, and Mannequin choices. Actors remains the default; Model retains the former Models/Mannequins behavior.
- Mannequin never writes igActor. Anim workaround is enabled by default: blended skeletal animation is sampled into igJoint / igMatrixObject / igVertexBlendMatrixListAttr scene-graph deformation. Rigid and UV animation keep their established scene-graph paths. Playback type and sampling options apply. With the workaround off, skeletal motion becomes a static pose at Static frame; UV animation remains independent.
- Export does not modify the source mesh, rig, action, or current frame.

## Validation
Blender 4.5.3 LTS: Alpha 4/6, explicit depth absence/off/on and edits; scene range and translation retention; RemovePos ordering; both custom rigs (wasted.blend and the supplied Max blend); seven sampled mannequin poses each, with maximum differences below 0.000135 Blender units; static mannequin snapshot; four playback modes; unchanged source geometry/rest matrices/frame; MUA Max pose/animation comparisons; custom clavicle repair and root-pivot regression; Wii and environment textures; helper export; Psylocke and fountain UV/geometry animation; self-illumination.

Native Alchemy 5 load/save: both alpha functions and depth states survive unchanged; both custom mannequin joint animations survive unchanged with no igActor; both FX exports load and save. RemovePos output matches the native batch's channel operations and values (the test corrects only the batch's inconsistent declared optimization count).

Fresh ZIP installation and upgrade over enabled 0.7.17 are checked separately. Existing 0.7.17 ZIP remains unchanged. These checks do not replace an in-game test of the new mannequin graph; no gameplay validation is claimed for it.

## Floating-rig idea — not implemented
A diagnostic could compare the skin's neutral foot contact and pelvis height with the intended animation reference, then preview one consistent calibration across the skin and the whole animation set (including idle). Do not apply a different offset per clip or silently move the mesh at export.
