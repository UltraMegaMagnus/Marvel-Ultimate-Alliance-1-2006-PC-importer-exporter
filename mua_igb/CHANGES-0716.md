# MUA IGB 0.7.16 validation and changes

Tested with Blender 4.5.3 LTS. The 0.7.15 ZIP is preserved unchanged.

## Changes

- Alchemy Shader exposes the emissive material colour as **Self-illumination**. Colour changes update the shading preview and exported material.
- Fixed 1201's import regression: source-less zero translation channels without a translation mask no longer replace bone rest offsets with the origin. Actual translation curves and explicitly enabled zero channels remain intact.
- Preserve named static transform helpers, including Gun1 and fx, as Blender empties. Export their names and world transforms even without associated geometry.
- Connect imported texture-animation actions to their Blender 4.5 node-tree slots so UV motion actually evaluates.
- Write vertex colours as igVec4ucList rather than an incompatible unsigned-integer vertex stream. The latter caused the native Alchemy loader to reject the FX exports even without animation.
- Preserve stationary siblings of animated scene nodes with an identity binding instead of attaching them to the first animated node on export.
- Read version-10 IGB sections, Skeleton2 data and Animation2 Enbaya streams, including separate translation quantization. Existing MUA animation decoding remains separate.
- Separate multi-character skin databases into asset collections. Gundam's clips use name matches or their own default-skeleton proportions for assignment; stale shared bindings are not blindly trusted.

## Supplied sample checks

| Sample | Checks |
| --- | --- |
| 1201 import wasted mua.igb | Five evaluated frames compared against 0.7.14; maximum coordinate difference 0.0000148 Blender units. |
| blade_shotgun.igb | Gun1 and fx imported and re-exported with matching names and transforms. |
| psylocke_blade_01.IGB | Four evaluated frames; geometry unchanged and UV motion matches after export/reimport. Native Alchemy 5 load/save succeeds. |
| fx_d_handfountain.igb | Four evaluated frames over 12 seconds; stationary and animated geometry preserved, maximum point error below 0.000001 Blender units; UV movement matches. Native Alchemy 5 load/save succeeds. |
| SpideySM_mesh__186EB4BC.igb | Main model, retained hidden shadow proxy, 41-bone skeleton, 275 actions. All actions evaluated at start/middle/end; 273 change at these sample points. External textures intentionally not required. |
| spidey 3 other file 3F789745.igb | 861 packed tile geometry assets imported separately; 22,736 triangles. No skeletal animation objects are stored in this library. |
| Gundam - Spirits Of Zeon.igb | 20 separate rigs/collections, 72 mesh objects after merging, 341 actions including standing poses. |
| Lupin / Digimon / Yu-Gi-Oh / Zentrix | Repeated original import tests: 2 / 24 / 1 / 108 actions, packed images and start/middle/end evaluation; previews compared. |

MUA regression checks cover the matched Max skin and three animation clips, Cage joint-axis repair, preservation of the original Blender mesh/rig, repeated root-offset-corrected export, Wii texture slots, environment maps, and blended embedded animation with all four playback modes. The matched Max animated vertex error stays below 0.00006 file units.

## Use and scope

Install the full mua_igb-0.7.16.zip. Reimport original files to obtain corrected UV-action connections, missing helpers, and the 1201 fix. Keep source scenes intact when testing.

Multi-asset files import into separate collections with only the first collection visible. Enable the desired collection in the Outliner. The existing export-all-scene behavior is unchanged: hidden collections are not an export filter; use a scene containing the assets you want to export.

The Spider-Man tile library is imported as individual geometry assets, not a reconstructed procedural city. Its packed normals are regenerated from geometry. Textures are external and may be assigned manually. Game-specific events, scripts, procedural roads and collision proxies are not converted into Blender animation.

Validation covers the supplied files and these layouts, not every possible file from these games. Native FX checks use the installed Alchemy 5 engine from C:/Alchemy; this is not a claim of an interactive MUA gameplay test.
