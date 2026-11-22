"""Repair identifiable MUA joint-axis mismatches without rebaking the mesh."""
import json
from pathlib import Path
import numpy as np


def _swing(a, b):
    a=a/np.linalg.norm(a);b=b/np.linalg.norm(b)
    cross=np.cross(a,b);cos=float(np.clip(a@b,-1,1))
    if cos > 1-1e-12:return np.eye(3)
    if cos < -1+1e-10:
        axis=np.eye(3)[np.argmin(abs(a))];axis=np.cross(a,axis);axis/=np.linalg.norm(axis)
        return 2*np.outer(axis,axis)-np.eye(3)
    x,y,z=cross;K=np.array([[0,-z,y],[z,0,-x],[-y,x,0]])
    return np.eye(3)+K+K@K/(1+cos)


def repair(skin):
    """Use a reference only to identify unambiguous longitudinal bone chains.

    Max Biped's local X points toward the next joint in these chains. A posed
    chain still satisfies this invariant: A/T-pose is not a classification.
    Preserve aligned frames and modest offsets (including authored roll).
    Only gross deviations exceeding 45 degrees trigger repair; small offsets
    also occur in known-good Max spine chains and are not errors. For a
    mismatched frame, swing the reference orientation toward the actual child.
    Nothing is inferred for unknown names, changed hierarchy, branching or
    zero-length chains. Mesh positions, weights and joint heads never change.
    """
    reference=json.loads((Path(__file__).parent/'data/actor_bind_reference.json').read_text(encoding='utf8'))['bones']
    by={b.name:i for i,b in enumerate(skin.bones)}
    fixed=[]
    for name,ref in reference.items():
        i=by.get(name)
        if i is None:continue
        frame=np.asarray(ref['frame'],float);head=np.asarray(ref['head'],float)
        choices=[]
        for cname,child in reference.items():
            j=by.get(cname)
            if child['parent']!=name or j is None or skin.bones[j].parent!=i:continue
            delta=np.asarray(child['head'])-head;length=np.linalg.norm(delta)
            if length>1e-6 and np.linalg.norm(frame.T@(delta/length)-[1,0,0])<1e-3:choices.append(j)
        if len(choices)!=1:continue
        bone=skin.bones[i];delta=skin.bones[choices[0]].head-bone.head;length=np.linalg.norm(delta)
        if length<1e-6:continue
        direction=delta/length
        if float(bone.frame[:,0]@direction)>=np.cos(np.deg2rad(45.0)):continue
        bone.frame=_swing(frame[:,0],direction)@frame
        fixed.append(name)
    if fixed:skin.repaired_bind_axes=tuple(fixed)
    return fixed
