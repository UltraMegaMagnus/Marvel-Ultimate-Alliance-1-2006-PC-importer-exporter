"""Final translation-channel removal matching the supplied RemovePos batch."""
BONES = ('Bip01 Spine', 'Bip01 Spine1', 'Bip01 Spine2', 'Bip01 Neck', 'Bip01 Head', 'Bip01 L Clavicle', 'Bip01 L UpperArm', 'Bip01 L Forearm', 'Bip01 L Hand', 'Bip01 L Finger0', 'Bip01 L Finger01', 'Bip01 L Finger1', 'Bip01 L Finger11', 'Bip01 R Clavicle', 'Bip01 R UpperArm', 'Bip01 R Forearm', 'Bip01 R Hand', 'Bip01 R Finger0', 'Bip01 R Finger01', 'Bip01 R Finger1', 'Bip01 R Finger11', 'Bip01 L Thigh', 'Bip01 L Calf', 'Bip01 L Foot', 'Bip01 L Toe0', 'Bip01 R Thigh', 'Bip01 R Calf', 'Bip01 R Foot', 'Bip01 R Toe0')

def apply(anim):
    selected = set(BONES)
    for name,(q,t) in list(anim.tracks.items()):
        if name.replace('_',' ') in selected:
            anim.tracks[name] = (q,None)
