UPDATE: constant root-channel removal described below is superseded in 0.7.10. Root translations now remain active even when constant. See WII-ROOT-0710.md.

# 0.7.8 — blended models, texture recovery and root channel semantics

## Models with animation

With Export Animation enabled and an active armature action, model export writes animation in both Actors and Models/Mannequins modes. Rigid pieces use scene transform nodes; blended skin uses a real actor, skin, skeleton, animation tracks, translation bitmask, combiner source links and an initialized playing state at full weight. The prior blended-weight rejection is removed. Existing user rig and mesh data are not rewritten.

Model controls include Sample rate (0 = scene FPS), Force sample, intervals and Repeat/Loop/Bounce/Clamp. Scene Start/End controls the exported interval. Supplied shuriken and Max animation use raw transform sequences; this release retains raw sequences for model output rather than assuming compressed model animation is required.

## Animation scene fidelity and root transitions

The supplied Max animation has no active Bip01 or Motion translation channels. Those constant translations belong to its skeleton. Previously forcing translation on every root could impose the preview rig's height on another skin and cause a pop.

An unchanged constant rest translation is now stored in the skeleton with its track translation disabled, following the Max convention. Deliberate constant offsets and varying translation remain active. RemovePos OFF does not remove limb motion or undo imported offsets using hidden metadata. Source rest-translation metadata no longer rewrites the exported scene skeleton. Constant rest channels are represented by skeleton data, not discarded. RemovePos ON continues to strip child-limb translation channels.

## Textures

Fresh import of both Wii materials populates the diffuse slots. A recovery path handles older .blend files where a real image is still on a known shader node but its Alchemy pointer is empty; it runs at registration, file load and object selection. Explicitly clearing a slot removes its node and therefore does not resurrect the image.

Environment unit 3 now populates the reflection property even when stored as an ordinary texture attribute rather than a cube-specific attribute. Six-face cube import remains supported. The supplied current Max2012 file contains eight images bound to units 0, 1, 2 and 5 only; there is no unit-3 environment map, reflection mask or cube attribute in that file. No environment image was invented or substituted.

## Validation and limits

Blender 4.5.3: both Wii material slots, legacy pointer recovery and explicit clearing; six environment faces and ordinary sphere-map binding; weighted model export/reimport at seven poses (maximum vertex error below 0.000006 metres); both model modes and all four playback modes at a requested 12 Hz; translation bitmasks, combiner source links and playing-state fields; Max static-root export on the custom rig; deliberate root offset preserved against evaluated Blender matrices despite legacy offset metadata; translation/rotation/nonuniform scale and single-frame regression checks.

Native state enums and field roles were checked in the supplied libIGSg.dll: kPlaying=0, kNone transition=4; the output also sets blend weight to 1 and connects each combiner bone to its live source. This is a structural check, not execution in the game. In-game playback and the reported visual transition should still be confirmed by the user. No claim of universal support for arbitrary unrecognized IGB classes or unrelated skeleton hierarchies is made.
