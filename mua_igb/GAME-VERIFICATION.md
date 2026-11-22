# Game executable and custom-rig verification — 0.7.2

## Conclusion and scope

The corrected attack_light3 has the translation mask, raw translation channel and Raven Motion metadata required by the installed MUA1 PC code. Native engine calls evaluate both exported root curves correctly. This verifies the relevant binary code paths and native track evaluation; it is not a whole-game playtest or a test of the native IGB file loader.

The test host reconstructed native igTransformSequence1_5 objects from the exported file's decoded time, quaternion, translation, channel and interpolation fields. It used the actual game DLL's constructor and exported methods in an isolated 32-bit PowerShell/C# process. The game installation and its binaries were not patched. No game binaries are included in the add-on ZIP.

## Binaries examined

- game.exe: D:/Games/MUA/game.exe (identical to the copy under Program Files (x86)/Activision/Marvel - Ultimate Alliance).
  SHA-256: 0e4a611d998ba0d855f8ca56925de0e7f53bf8e6b9884d0a5cb267757c31f1cf
- libIGSg.dll: D:/Games/MUA/libIGSg.dll (identical to the newly supplied Source/libIGSg.dll).
  SHA-256: b1457cc11b72e1d63c6d0247cd72cc66d1eb2a09a407f2d01ce2b7b777362658

Addresses below are preferred virtual addresses in those binaries.

## Bip01 translation

libIGSg.dll at 0x10020790 implements igAnimation::getUseAnimationTranslationBool. It reads the mask at object offset 0x38, indexes words by track_index >> 5, and tests 1 << (track_index & 31). The null-mask branch at 0x100207BD returns false.

The raw-source translation accessor at 0x10020DB0 checks bit 0 of drivenChannels at offset 0x48 before returning translation data. The corrected animation sets both the animation-level bit and the sequence-level bit for Bip01 and Motion. Bip01 has a key at time zero.

Native getQuaternionAndTranslation (0x1001B730, implementation 0x10042380) sampled every exported root key and every midpoint: 31 times per track. Bip01 and Motion each differed from expected translation by no more than 0.00000190735 file units. The native mask getter returned true for their actual exported track indices and false with a null mask.

## Motion metadata

The game code calls igAnimation::getAnimationTrack, getConstantQuaternion and getConstantTranslation at 0x0063D7F5, 0x0063D811 and 0x0063D8FE. The code checks that the absolute fourth component lies strictly between 998 and 1000, copies the first three components into its motion vector, calculates its magnitude, and reads the constant translation. Negative marker values set the flag at destination offset 0x1C. The exported -999 marker meets these checks.

For attack_light3, the corrected export retains the vanilla values exactly:
- Motion velocity/marker: (57.221336364746094, 0, 0, -999)
- Motion constant translation: (-6.4029998779296875, 0, 0)
- Duration: 0.75 seconds, 16 source samples.

Existing extracted Motion metadata now survives export even with Extract Motion disabled. Explicit extraction remains available for newly authored travel. Edited travel recomputes the velocity; uniform object scale also scales the stored offset.

## Custom skeleton compatibility fixes

1. Export now samples evaluated pose matrices, including constraints and drivers, instead of only pre-constraint matrix_basis. A Copy Rotation regression reproduced the constrained pose after export/import without the constraint, including descendant bones.
2. Uniform armature object scale now affects animated displacement consistently with skin bind positions. Non-uniform/mirrored armature object transforms that cannot safely be represented are rejected with an Apply Scale instruction instead of silently producing distorted output.
3. The previous scale channel flag 4 was Euler rotation, not scale. Scale now uses flag 8, linear scale interpolation and the correct Blender-to-Alchemy axis order. Native getMatrix produced scale (1.2, 1, 0.8) for Blender scale (0.8, 1.2, 1), as required by the bone-axis mapping.
4. Custom weighted Motion/Footsteps bones are no longer excluded from inverse bind matrices solely because of their names. Custom nonidentity helper orientations are retained as well.
5. Imported curve paths use Blender's escaped bone paths, supporting bone names containing quotes or backslashes.

The DLL's binding path (0x10064E80, especially 0x10064EF7–0x10064F28) matches target skeleton bones to animation track names. A custom skin and its animations must therefore use matching names and compatible hierarchy/rest semantics. Arbitrary limb names do not require vanilla-axis snapping. Raven locomotion still requires the engine-recognized Motion track; an add-on cannot remove game-side naming rules for locomotion, attachments or other gameplay features. This is not automatic retargeting between unrelated rigs.

## Blender regression results

Blender 4.5.3 LTS tests passed for all eight original model samples, the later PSP environment skin, all 48 earlier animation exports, attack_light3 raw/compressed exports, custom rest axes, materials, alpha, packed textures, skin export after animation, scale channels and root extraction. Constraint baking, scaled displacement and a Motion-weighted custom skin passed additional tests.

The now-supplied Source/10_archangel.igb contains igCompressedAnimationSequenceQS tracks. All 10 actions imported onto the prototype skin and exported successfully. This replaces the earlier missing-sample limitation for that file. The separate Raven synthetic decoder fixture is a different test; the original file is QS, not Raven.

Full gameplay, transitions, physics and every possible custom rig are not established by these checks. Export matching custom skins and animations together; apply non-uniform armature object scale before export.
