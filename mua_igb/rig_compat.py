"""Prepare a custom bind pose for an existing, explicitly selected game rig.

This is an intentional mesh/rest-pose conversion, never an implicit export-axis snap.
"""
import os
import numpy as np
import bpy
from mathutils import Matrix
from .mua_model import load_model
from . import pose_math as P


def prepare(context, arm, reference_path):
    if arm is None or arm.type != 'ARMATURE':
        raise ValueError('Select the custom armature or one of its skinned meshes')
    if arm.mode != 'OBJECT':
        raise ValueError('Switch to Object Mode before preparing the bind pose')
    if arm.animation_data and (arm.animation_data.action or len(arm.animation_data.nla_tracks)):
        raise ValueError('Prepare the bind pose before assigning animations; use an unanimated copy of the rig')
    if any(p.constraints for p in arm.pose.bones):
        raise ValueError('Prepare an unanimated deform rig without pose constraints')
    if any(not np.allclose(np.array(p.matrix_basis), np.eye(4), atol=1e-6) for p in arm.pose.bones):
        raise ValueError('Clear the pose transforms before preparing the bind pose')
    if not np.allclose(np.array(arm.matrix_world)[:3,:3], np.eye(3), atol=1e-6):
        raise ValueError('Apply armature object rotation and scale before reference-pose preparation')
    model = load_model(reference_path, 'NONE')
    ref = {b.name: b for b in model.bones}
    ref_index = {b.index: b for b in model.bones}
    old = {b.name: np.array(b.matrix_local) for b in arm.data.bones}
    matched = set(old) & set(ref)
    if len(matched) < 2:
        raise ValueError('The reference skin needs matching bone names')
    meshes = []
    for ob in context.scene.objects:
        if ob.type != 'MESH':
            continue
        mods = [m for m in ob.modifiers if m.type == 'ARMATURE' and m.object == arm]
        if not mods:
            continue
        if len(mods) != 1 or mods[0].use_deform_preserve_volume or mods[0].use_bone_envelopes or mods[0].vertex_group:
            raise ValueError('Use one vertex-group Armature modifier with Preserve Volume off: ' + ob.name)
        meshes.append(ob)
    if not meshes:
        raise ValueError('No meshes use the selected armature')
    target, order = {}, []
    def build(b):
        if b.name in target:
            return
        if b.parent:
            build(b.parent)
        m = old[b.name].copy()
        if b.name in ref:
            rb = ref[b.name]
            m[:3,:3] = rb.rot_world @ P.C
            if b.parent and b.parent.name in ref:
                rp = ref[b.parent.name]
                actual_parent = ref_index.get(rb.parent)
                if actual_parent is not None and actual_parent.name != b.parent.name:
                    raise ValueError('Reference parent differs for bone ' + b.name)
                delta = rb.pos_world - rp.pos_world
                length = np.linalg.norm(old[b.name][:3,3] - old[b.parent.name][:3,3])
                if np.linalg.norm(delta) > 1e-8:
                    m[:3,3] = target[b.parent.name][:3,3] + delta / np.linalg.norm(delta) * length
                else:
                    m[:3,3] = target[b.parent.name][:3,3] + old[b.name][:3,3] - old[b.parent.name][:3,3]
            elif b.parent:
                m[:3,3] = (target[b.parent.name] @ np.linalg.inv(old[b.parent.name]) @ old[b.name])[:3,3]
        elif b.parent:
            m = target[b.parent.name] @ np.linalg.inv(old[b.parent.name]) @ m
        target[b.name] = m
        order.append(b.name)
    for b in arm.data.bones:
        build(b)
    changes = {name: target[name] @ np.linalg.inv(old[name]) for name in order}
    # Prepare every array before changing Blender data, so validation cannot leave a half-converted rig.
    updates = []
    for ob in meshes:
        mesh = ob.data
        into = np.array(arm.matrix_world.inverted() @ ob.matrix_world)
        back = np.linalg.inv(into)
        mats = {name: back @ t @ into for name,t in changes.items()}
        names = {g.index:g.name for g in ob.vertex_groups}
        blended = np.zeros((len(mesh.vertices),4,4), np.float64)
        for v in mesh.vertices:
            total = 0.0
            for g in v.groups:
                name = names.get(g.group)
                if name in mats and g.weight > 0:
                    blended[v.index] += g.weight * mats[name]
                    total += g.weight
            blended[v.index] = blended[v.index] / total if total else np.eye(4)
        if np.any(abs(np.linalg.det(blended[:,:3,:3])) < 1e-8):
            raise ValueError('Singular deformation while preparing ' + ob.name)
        def convert(collection):
            coords = np.empty(len(collection)*3, np.float64)
            collection.foreach_get('co',coords)
            return (np.einsum('nij,nj->ni',blended[:,:3,:3],coords.reshape(-1,3))+blended[:,:3,3]).astype(np.float32)
        coords = convert(mesh.vertices)
        keys = [convert(k.data) for k in mesh.shape_keys.key_blocks] if mesh.shape_keys else []
        normals = None
        if mesh.has_custom_normals:
            ns = np.array([n.vector[:] for n in mesh.corner_normals])
            vi = np.array([loop.vertex_index for loop in mesh.loops])
            normals = np.einsum('ni,nij->nj',ns,np.linalg.inv(blended[:,:3,:3])[vi])
            normals /= np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-12)
        updates.append((ob,coords,keys,normals))
    if arm.data.users > 1:
        arm.data = arm.data.copy()
    for ob,coords,keys,normals in updates:
        if ob.data.users > 1:
            ob.data = ob.data.copy()
        ob.data.vertices.foreach_set('co',coords.ravel())
        for key,values in zip(ob.data.shape_keys.key_blocks if ob.data.shape_keys else [],keys):
            key.data.foreach_set('co',values.ravel())
        ob.data.update()
        if normals is not None:
            ob.data.normals_split_custom_set(normals.tolist())
    context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for name in order:
        bone = arm.data.edit_bones[name]
        length = bone.length
        bone.use_connect = False
        bone.matrix = Matrix(target[name])
        bone.length = length
    bpy.ops.object.mode_set(mode='OBJECT')
    arm['mua_reference_bind'] = os.path.abspath(reference_path)
    context.view_layer.update()
    return {'matched_bones':len(matched), 'meshes':len(meshes)}
