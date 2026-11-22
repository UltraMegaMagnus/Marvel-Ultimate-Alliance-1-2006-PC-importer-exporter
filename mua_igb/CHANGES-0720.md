# 0.7.20 - consistent manual color controls

Both model import and model export now show a Swap color checkbox, disabled by default. Import passes SWAP only when checked, otherwise NONE. Export continues to use the current Blender image pixels and its own checkbox only; it does not consult how the image was imported. Normal maps are excluded from red/blue swapping in both directions. Export does not alter the original Blender images.

The export type label Actors is now Actor. Model and Mannequin are unchanged.

Only operator labels, tooltips, and the import checkbox argument mapping changed; the texture writers, model/animation exporters, shader implementation, and rig fixes are byte-for-byte unchanged from 0.7.19.

Validation in Blender 4.5.3: unchecked defaults and matching labels; all four import/export swap combinations for Actor, Model, and Mannequin using skin vanilla with env.igb; diffuse, specular, emissive/reflection textures and environment faces; identical normal maps for all combinations; original Blender images unchanged. Fresh installation and enabled-version upgrade are checked in an isolated E: profile. 0.7.19 ZIP preserved.
