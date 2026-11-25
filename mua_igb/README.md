# Update 0.7.28

Geometry exported with bone weights always enables Vertex Blend State, including when a reused material explicitly disables it.

# Update 0.7.27

Vertex Blend State is now handled automatically and its checkbox is hidden in Alchemy Shader.

# Update 0.7.26

Global Color State and Vertex Blend State each have one material checkbox. New and newly converted Alchemy materials enable Global Color State. Imported materials retain their authored states. Mannequin export now labels its existing option Animation workaround.

# Update 0.7.25

Alchemy Shader exposes per-unit texture coordinate and function attributes, material global color and vertex blend states, and cube apply mode. A cube can retain its original sphere fallback. Animation imports append source playback and original Motion data to AnimationImportReport in the Blender Text Editor.

# Update 0.7.24

Alchemy Shader can import, edit and export Material Mode. New materials omit this attribute. RemovePos and its optional final Enbaya compression now start enabled.

# Update 0.7.23

Actor exports a rigged skin by default, even with an action active. Model and Mannequin default to exporting animation. Actor material UV scrolls export independently. Generate mipmaps is optional on all three. Posing imports can transfer Motion onto Bip01. For raw animation export with RemovePos, optional final Enbaya compression preserves the raw Motion track and scale-keyed tracks.

# Update 0.7.22

See [CHANGES-0722.md](CHANGES-0722.md) for the animation import modes and new-material default.

# Update 0.7.21

See [CHANGES-0721.md](CHANGES-0721.md) for converted-file and special-character import support. Export behavior is unchanged.

# Update 0.7.20

See [CHANGES-0720.md](CHANGES-0720.md) for the manual swap controls and Actor label.

# Update 0.7.19

See [CHANGES-0719.md](CHANGES-0719.md) for the current animation controls, fixes, and validation.

# Update 0.7.18

See [CHANGES-0718.md](CHANGES-0718.md) for the current controls, mannequin behavior, and validation limits.

# MUA IGB tools 0.7.17 â€” Blender 4.5

Install mua_igb-0.7.17.zip with Preferences > Add-ons > Install from Disk, replacing the previous mua_igb add-on. Restart Blender. Reimport the original IGB files to obtain the corrected animation curves and all imported environment slots; exporting an already retargeted older Blender Action cannot undo its previous retargeting.

## Changes in 0.7.17

Alchemy Shader controls for vertex-colour export, TextureMatrixState, ShaderParameters and sample-matching Alpha. See SHADER-CONTROLS-0717.md. No retargeting feature added.

## Changes in 0.7.16

Self-illumination colour, 1201 regression fix, effect helper preservation, working UV animation and native-loadable coloured FX exports, Spider-Man 3 Wii imports, and separate Gundam rigs/actions. See CHANGES-0716.md for validation and scope.

## Changes in 0.7.15

Add tested model, texture and animation import paths for the supplied Lupin III, Digimon World 4, Yu-Gi-Oh and Zentrix assets. Preserve MUA export fixes. See ADDITIONAL-GAMES-0715.md for sample results and scope.

## Changes in 0.7.14

Automatically repair identifiable gross joint-axis mismatches while preserving authored skin pose, geometry and weights. Retain the root-offset correction. See BIND-AXES-0714.md.

## Changes in 0.7.13

Preserve authored Actors mesh and bind pose instead of forcing the reference T-pose. Retain the root/pelvis offset correction. Blender originals remain unchanged. See POSE-PRESERVATION-0713.md.

## Changes in 0.7.12

Replaces fixed actor root height with a Bip01 pivot aligned to the actual pelvis, matching the supplied Max skin. Corrects the 3.78442-unit bind gap in the failing skin without editing your Blender rig or mesh. See ROOT-PIVOT-0712.md for validation and limits.

## Changes in 0.7.11

Automatic detached Bip01 bind-height preparation for static Actors; live default animation translations; optional whole-set animation adaptation. Corrected FX transform targets, bounds, timing and UV sampling. Re-export affected skins as Actors with Export Animation and Compensate Height off. See ROOT-FX-0711.md for tests and limits.

## Changes in 0.7.10

Corrected Wii fixed-function emission preview and restored explicit constant pelvis/root translation, avoiding destination-rest fallback. See WII-ROOT-0710.md.

## Changes in 0.7.9

Installation-only repair: defer material recovery until Blender finishes enabling the add-on; clean stale classes, menus and handlers after interrupted enables; reload retained submodules during ZIP upgrades; roll back partial registration on failure. Import/export behavior is unchanged from 0.7.8.

## Changes in 0.7.8

Blended embedded animation, model sample-rate/playback controls, Max-compatible constant root channels, scene-faithful animation export, legacy texture-slot recovery and ordinary environment-map bindings. See BLENDED-ROOT-TEXTURES-078.md.

## Changes in 0.7.7

Scene Start/End controls animation export. RemovePos WIP defaults off; Compensate Bip01 Height WIP renamed; forced intervals default to 1. Rigid embedded model animations now export as live scene nodes. See RANGE-AND-EMBEDDED-077.md.

## Changes in 0.7.6

Replaces root-offset rebasing with compatible child-joint preview offsets; retains original pelvis/Motion animation and exports RemovePos without imposing preview-rig proportions. See ANIMATION-COMPATIBILITY-076.md for tests and limits.

## Changes in 0.7.5

Custom-rig root translation rebasing, source animation rest offsets preserved on export, missing Motion track preservation, neutral action channels, scene-only object selector, and material-aware mesh merging. See ANIMATION-OFFSETS-075.md.

## Changes in 0.7.4

Automatic non-destructive Actors bind preparation, preserving custom proportions and extra bones. Shader creation now immediately removes unused texture nodes. See CUSTOM-RIG-FIX.md for behavior, validation and compatibility limits.

## Changes in 0.7.2

Verified attack_light3 root-track evaluation with the actual game libIGSg.dll and inspected the game.exe Motion handling. See GAME-VERIFICATION.md for addresses, hashes, methods and limits.

Custom-rig fixes include constraint/driver pose sampling, uniform armature-scale displacement, the correct native scale flag and axis order, preserving weighted Motion/helper bind data, and escaped bone animation paths. Existing extracted Motion metadata survives export without extracting twice. Apply non-uniform armature object scale before exporting; unsafe rest transforms produce a clear error.

The original prototype 10_archangel.igb is now tested: all 10 QS actions imported and exported.

## Changes retained from 0.7.1

- Imported armatures are named ActorRoot (Blender adds a suffix when that name already exists).
- Same-name mesh joining consolidates equivalent Alchemy materials and remaps polygon material indices, preserving weights and UVs. Distinct materials and independently animated UV materials stay separate.
- No vanilla-axis snapping, automatic leg-length scaling, or automatic Motion-bone insertion. Actual skeleton rest orientations and hierarchy are retained. Support for Vanilla skeleton keeps source translation channels 1:1; Support for all skeletons keeps root/pelvis translation and strips child-limb translation.
- Unnamed PSP geometry uses its named skin parent (15301 for the new sample). Distinct segments remain separate objects, with original names remembered for export.
- All six cubemap directions are populated even if images happen to be identical. The original stored face orientation and order are preserved on export. Sphere-only PSP reflections remain sphere maps rather than being duplicated into six cube faces. The Environment Map section exposes the mapping type.
- The Alchemy Shader texture controls create and remove actual shader texture nodes automatically. No placeholder images or empty texture nodes are generated. Environment/emissive preview strength is enabled. Existing 0.7.0 node names are migrated when synchronized.
- Dump Animated Mesh and its related unsupported controls are removed.
- Raw and compressed animation exports both write the animation translation bitmask. Raw Bip01 sequences have the translation channel enabled and a key at time zero. Missing masks were the cause of the engine ignoring the previous export's stored height curve.
- RemovePos uses root/pelvis roles and hierarchy instead of a fixed limb blacklist. Hierarchy roots and Bip01/Motion/root/pelvis/hips keep translation; other child bones do not. Disable RemovePos to retain all translation channels.
- Extract Motion moves linear horizontal Bip01 travel into Motion when Motion does not already carry travel. Bip01's vertical bounce stays keyed. Velocity Z remains zero. Existing extracted Motion metadata is retained when its velocity still matches the evaluated action; edited travel produces a newly computed velocity.
- Imported sample rates are the default export rate. This avoids the old 20-to-30 FPS resampling of the supplied attack. Source Motion velocity and constant translation offset survive both raw and compressed exports.

## Existing features

Textures are packed into the blend. Swap colors never affects normal maps. Mesh names have no model prefix. Alpha test is separate from Blend. Skin Segment creates actual igSegment nodes. Actors enables Generate Global Color by default; Models/Mannequins exports static geometry and optionally generates igCollideHull. All scene meshes are included. Modelling modifiers and shape keys are evaluated without baking the armature's current pose into the skin.

Wii/PSP geometry and textures, XML2 environment shaders, prototype Raven tracks, embedded node/UV animation, vertex colors, skin weights and raw animation scales are supported. Raw keyframes remain the default export format. Compressed Enbaya export rejects scale tracks instead of silently dropping them.

See VALIDATION.md for checks and verification limits.
