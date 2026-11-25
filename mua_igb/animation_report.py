"""Human-readable source data for actions imported in Blender."""
import math
import bpy
from .playback import PLAY_MODE


def describe(an, action, start, fps, rest):
    names = {v: k.title() for k, v in PLAY_MODE.items()}
    modes = {}
    for bone, mode in an.play_modes.items():
        modes.setdefault(names.get(mode, f"Unknown ({mode})"), []).append(str(bone))
    playback = ", ".join(sorted(modes)) or "Unspecified in source"
    end = start + an.duration * fps
    lines = [f"Animation name: {an.name}", f"Blender action: {action.name}", f"Animation type: {playback}",
             f"Frame range: {start:g} to {end:.9g} ({fps:.9g} fps, {an.duration:.9g} seconds)",
             f"Whole frames in range: {max(0, math.floor(end + 1e-6) - start + 1)}",
             f"Tracks matching rig: {sum(t.bone in rest for t in an.tracks)} of {len(an.tracks)}"]
    if len(modes) > 1:
        lines.extend(f"  {key}: {', '.join(bones)}" for key, bones in modes.items())
    motion = next((t for t in an.tracks if t.bone == "Motion"), None)
    if an.motion_fields is not None:
        rot, pos = an.motion_fields
        label = "Raven velocity XYZW" if an.motion_velocity is not None else "track vector XYZW"
        lines.extend([f"Original Motion {label}: {rot}", f"Original Motion offset XYZ: {pos}"])
    if motion is not None:
        def fmt(values):
            return "(" + ", ".join(format(float(v), ".9g") for v in values) + ")"
        lines.append("Original Motion keys (source units, before transfer or posing)")
        for i, t in enumerate(motion.times):
            pos = fmt(motion.trans[i]) if motion.trans is not None else "no translation channel"
            lines.append(f"  {t:.9g} s: XYZ {pos}, quaternion XYZW {fmt(motion.quats[i])}")
    elif an.motion_fields is None:
        lines.append("Original Motion: no Motion track")
    return "\n".join(lines)


def append(source, armature, entries):
    text = bpy.data.texts.get("AnimationImportReport")
    if text is None:
        text = bpy.data.texts.new("AnimationImportReport")
    text.use_fake_user = True
    text.cursor_set(len(text.lines) - 1, character=len(text.lines[-1].body))
    text.write(f"Source: {source}\nArmature: {armature}\n\n" + "\n\n".join(entries) + "\n\n")
