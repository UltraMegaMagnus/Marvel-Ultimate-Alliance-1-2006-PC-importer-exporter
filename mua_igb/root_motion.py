"""Represent actor displacement below the skin-dependent Bip01 pivot."""
import numpy as np

def normalize_actor_root(skin):
    """Make the unweighted Bip01 control pivot coincide with its pelvis.

    All child world bind matrices and mesh arrays stay unchanged. The skeleton
    writer consequently recomputes the root->child translation without moving
    the mesh or any child joint to a hardcoded character height.
    Max's actor bind has a zero root-to-pelvis offset. A fixed world root height
    creates a nonzero offset on other rigs, which jumps when a clip switches
    between a masked-off pelvis translation and an explicit zero translation.
    """
    indices = {b.name.casefold():i for i,b in enumerate(skin.bones)}
    ri,pi = indices.get('bip01'),indices.get('bip01 pelvis')
    if ri is None or pi is None or skin.bones[pi].parent != ri:
        return []
    root=skin.bones[ri]
    if any(np.any((np.asarray(m.joints)==ri)&(np.asarray(m.weights)>1e-6)) for m in skin.meshes):
        return ['warning: retained Bip01 bind pivot because it has vertex weights; moving a deforming pivot would change animation deformation.']
    target = skin.bones[pi].head
    if np.array_equal(root.head,target):
        return []
    old=root.head.copy();root.head=target.copy()
    skin.normalized_root_pivot = (tuple(old),tuple(target))
    return []
