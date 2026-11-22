SUPERSEDED: root-offset rebasing described below was removed in 0.7.6. See ANIMATION-COMPATIBILITY-076.md.

# Animation offsets and object selector — 0.7.5

Support for all skeletons now applies root/pelvis motion as a displacement from the source animation's stored rest translation to the target rig's rest translation. Limb translations remain excluded in this mode. It does not flatten vertical bounce or use each clip's first frame as a guessed rest pose. Vanilla mode keeps absolute translations.

The exporter retains the imported animation's source skeleton offsets instead of substituting the preview skin's offsets. When exporting a fitted action, it reverses only the import-time translation offset, so edits to movement remain intact. If the preview rig has no Motion bone, its imported Motion track is retained on the action and emitted on export without adding a Blender bone. Source Motion velocity/offset are retained. Missing action channels get explicit neutral keys to prevent state from a previous action leaking into idle.

The supplied new attack export had source pelvis height 42.0295 while the original animation used 20.9148, despite effectively identical translation keys. That older export cannot be corrected generically without its original source rest information. The included corrected attack is re-exported from the supplied original attack using this version. Reimport original animations with the updated addon; already imported actions do not acquire the new metadata automatically.

The Alchemy object selector now shows only mesh objects linked to the current scene, excluding orphan entries marked 0. No global orphan purge is performed. Name-based mesh merging includes the material signature: matching materials consolidate, different materials keep separate meshes. Slot deduplication still remaps polygon indices.

Tests in Blender 4.5.3: supplied customrigwasted and vanilla environment skin, attack_light3 original and supplied idle, both support modes, translation samples and source rest round trips, Motion preservation with and without a Motion bone, action switching, current-scene selector filtering, and actual importer merging of equal-name/equal-material versus different-material pieces. The attack end/idle start pelvis displacement differs by less than 0.001 game units after rebasing. These are Blender and file-level tests; the game was not launched. This fixes translation/reference-offset handling; it does not infer arbitrary semantic bone-name mappings.
