"""Conservative selection of native playback versus humanoid fitting."""
import hashlib
import json
import re
from pathlib import Path


def bind_signature(arm):
    data = [(b.name, b.parent.name if b.parent else None,
             [float(v) for row in b.matrix_local for v in row]) for b in arm.data.bones]
    data.append([float(v) for row in arm.matrix_world for v in row])
    return hashlib.sha256(json.dumps(data).encode('utf-8')).hexdigest()


def remember(arm, skeleton_name):
    # Raven skin identifiers end in a two-digit costume number. Use the
    # skeleton's authored name, never the disk filename or a creature list.
    match = re.fullmatch(r"(\d{3,})_skel", skeleton_name or '', re.IGNORECASE)
    if match:
        arm['mua_native_skin_id'] = match.group(1)
        arm['mua_native_actor_family'] = str(int(match.group(1)[:-2]))
        arm['mua_import_bind_signature'] = bind_signature(arm)


def native_reason(arm, anims, rest):
    reference = json.loads((Path(__file__).parent/'data/actor_bind_reference.json').read_text(encoding='utf-8'))['bones']
    # Auxiliary effect bones and finger tips do not classify body topology.
    core = ['Bip01 Pelvis','Bip01 Spine','Bip01 Spine1','Bip01 Spine2','Bip01 Neck','Bip01 Head']
    core += ['Bip01 '+side+' '+part for side in ('L','R')
             for part in ('Clavicle','UpperArm','Forearm','Hand','Thigh','Calf','Foot')]
    for name in core:
        if name in rest and reference[name]['parent'] is not None:
            if rest[name][2] != reference[name]['parent']:
                return 'nonhumanoid bone hierarchy'
    if not any(name in rest for name in core):
        return 'no humanoid reference hierarchy'
    family = arm.get('mua_native_actor_family')
    if family and arm.get('mua_import_bind_signature') == bind_signature(arm):
        # Explicit cross-family aliases from the supplied NPC definitions
        # (for example Apocalypse's separately numbered dummy animation set).
        aliases = json.loads((Path(__file__).parent/'data/native_actor_aliases.json').read_text())
        accepted = aliases.get(arm.get('mua_native_skin_id'), [])
        def matches(anim):
            names = getattr(anim,'skeleton_names',())
            if any((name or '').lower() in accepted for name in names):
                return True
            return any((m := re.match(r"^(\d+)_", name or '')) and str(int(m.group(1))) == family for name in names)
        if anims and all(matches(anim) for anim in anims):
            return 'unchanged imported rig and matching character animation family'
    return None
