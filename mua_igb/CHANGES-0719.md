# 0.7.19 - animation export corrections

- Rename RemovePos WIP to RemovePos. Default remains disabled; the exact supplied batch bone list and final-operation ordering are unchanged.
- Extract Motion now runs only when explicitly enabled. Imported Motion metadata no longer enables extraction implicitly. With extraction off, igAnimationTrack Motion velocity and offset fields are zero, while sampled bone translation/rotation keys are retained.
- Remove the Adapt Bip01 height to skin option and its animation-export implementation. Existing Actor skin bind/root corrections remain unchanged.
- Standalone animation export now honors the playback selector; it previously hardcoded native Clamp regardless of the UI selection.
- Centralize the native playback mapping: Repeat=0, Clamp=1, Bounce=2, Loop=3. Verified against idleVANILLA (Repeat) and the supplied Max loop/bounce/clamp files. idleTEST contained Clamp=1 on every raw sequence.
- Apply the same mapping to raw tracks, compressed Enbaya sources (including resampling), embedded blended Actors/Models, mannequin joints, rigid animated models, and UV animation. Previous Loop/Clamp mappings were reversed in the embedded export paths.

## Validation
Blender 4.5.3: all four playback choices in raw/compressed animation export; Motion extraction on/off with RemovePos on/off; deliberately stale imported Motion metadata; exported translation masks; scene frame preservation; renamed/removed operator controls; existing shader/depth tests; default translation retention and frame range; original-pose Max export; custom-rig clavicle and root-pivot regressions; Wii and environment textures; blended Actors/Models; all four mannequin playback modes and seven evaluated poses; both custom rigs' animated/static mannequin exports without scene mutation; Psylocke/fountain geometry and UV animation; self-illumination; all four playback choices for Psylocke UV and shuriken rigid animation.

Native Alchemy 5 load/save validated all eight raw/compressed idle mode exports. Selected playback values and zero Motion metadata survive native serialization. Native enum values match the supplied reference files. In-game playback has not been directly tested in this session.

The 0.7.18 ZIP is preserved. Fresh installation and upgrade over enabled 0.7.18 are tested in an isolated profile on E:.
