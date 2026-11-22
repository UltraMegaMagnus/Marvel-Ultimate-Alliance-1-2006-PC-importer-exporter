# 0.7.15: additional Alchemy game imports

Validated the four user-supplied samples in Blender 4.5.3:

| Sample | Actions | Texture handling |
| --- | ---: | --- |
| Lupin III: Lupin is dead, Zenigata is in Love | 2 | PS2 4-bit palettes and alpha; inherited QS tracks and constant helper orientations |
| Digimon World 4 | 24 | DDS texture and Enbaya skeletal animation |
| Yu-Gi-Oh! The Dawn of Destiny | 1 embedded action, 67 node/joint tracks | Older Xbox IGB v5 info-index layout; colour, mask and reflection shader stages |
| Zentrix | 108 | PS2 palettes, environment shader, rigid bone-local geometry, combiner orientations |

Textures are packed into the blend. The original source files are not changed. Native coordinates, names and timing are retained; these games need not use MUA's scale or up axis. Original separate skins in older multi-skin assets receive separate collections. Explicit shadow skins remain available but are hidden from normal viewport/render display; enable their objects in the Outliner if needed. Constant animation tracks are retained instead of silently discarded. Missing inverse-bind orientations can be recovered from consistent constant tracks or the rigid actor combiner.

The older environment shader maps reflection/diffuse stages to their correct roles rather than treating stage 1 as a MUA normal map. Yu-Gi-Oh's scalar mask and reflection are exposed in the Alchemy panel. Blender previews approximate the source game's custom lighting; this does not emulate its Xbox GPU program or game logic. This release adds import support and does not add exporters for those original games.

## Debug XBE evidence

The supplied default-dbg.xbe is an Xbox XBEH image with Batman XboxDebug PDB and Alchemy 2.5 source paths. Native QS encoding sets sign bit 0x8000 at VA 0x1764c2 and shifts the magnitude by 23 at VA 0x1764e7. This independently supports the decoder's existing signed 2^23-nanosecond QS timing. It is not evidence that MUA and Batman use identical engine behavior, nor does it provide Lupin's game-specific runtime. The decoded Lupin subclass uses its inherited QS channel layout; game events and custom runtime behavior are not imported. The executable is not redistributed.

## Validation scope

All four samples were imported and saved as blends in Blender 4.5.3; every action was evaluated at its start, midpoint and end, with finite bone matrices, expected action counts, packed finite image data and no reported import warnings. Textured previews were inspected. These results establish support for the supplied assets and observed layouts, not every possible file from each title. Existing MUA actor-axis and root-offset exporter code is unchanged.

Detailed JSON and the debug-reference notes accompany the package under validation/0715_*. Existing regression checks cover the matched Max skin, Cage joint repair, root-offset/repeated export, Wii/PSP/XML inputs, texture handling, blended embedded animation and ZIP installation/upgrade.
