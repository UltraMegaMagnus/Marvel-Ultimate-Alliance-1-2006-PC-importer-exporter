# Shader controls in 0.7.17

Alchemy Shader now includes:

- Vertex color: the existing per-object colour-stream export switch, also accessible here. The actual colours remain mesh color attributes, editable with Blender's colour tools. Changing this switch now reads the original object's current setting rather than a stale evaluated copy.
- TextureMatrixState: enables/disables unit-0 texture-coordinate transforms. Disabling mutes the UV mapping preview and exports an explicit false state without deleting the animation. Imported on/off states are retained in material matching.
- ShaderParameters: an explicit section for material.reflectance (four-component vector) and material.transparent (boolean), as found in the supplied glass sample. These values are exported even without an environment texture when enabled. Disabling the section omits those constants. Boolean values are decoded separately from the constant's enabled flag.
- Alpha: a simple on/off control for igAlphaStateAttr. New/default settings are function 6, reference 0.5, matching the supplied cable sample. Explicitly imported function/reference values remain intact and are displayed read-only. Alpha remains independent from framebuffer Blend and the glass shader's transparent flag.

Validation: Blender 4.5.3 source import/edit/export checks for both supplied files, Psylocke vertex colour and UV-state toggles, both FX geometry/UV roundtrips, the matched Max skin and three clips, Cage axis repair, root-offset and repeat-export checks, Wii/environment textures, and blended embedded animation. Glass and alpha exports pass native Alchemy 5 load/save. Fresh installation and enabled-addon upgrade are tested separately in an isolated E: profile.

The 0.7.16 FX/helper changes are generic code paths for supported IGB node/UV animations and named static transform helpers; there are no sample-filename conditions. This does not establish support for every engine-specific node type. No animation retargeting feature was added in this release.
