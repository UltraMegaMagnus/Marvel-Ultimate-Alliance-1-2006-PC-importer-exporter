"""Prepare detached export arrays for MUA actor animations; never edit bpy data."""
import json
from pathlib import Path
import numpy as np


def prepare(skin):
    reference = json.loads((Path(__file__).parent / 'data' / 'actor_bind_reference.json').read_text(encoding='utf8'))['bones']
    bones = skin.bones
    matched = {i for i, b in enumerate(bones) if b.name in reference}
    if len(matched) < 2:
        return ['No matching MUA bind skeleton: retained custom bind pose; existing game animations require matching bone names.']
    old = np.tile(np.eye(4), (len(bones), 1, 1))
    for i, b in enumerate(bones):
        old[i, :3, :3], old[i, :3, 3] = b.frame, b.head
    target, visiting = {}, set()
    def build(i):
        if i in target:
            return target[i]
        if i in visiting:
            raise ValueError('Cyclic actor bone hierarchy')
        visiting.add(i)
        b = bones[i]
        parent = b.parent
        m = old[i].copy()
        if parent >= 0:
            m = build(parent) @ np.linalg.inv(old[parent]) @ m
        if i in matched:
            r = reference[b.name]
            m[:3, :3] = r['frame']
            if parent in matched:
                delta = np.array(r['head']) - reference[bones[parent].name]['head']
                distance = np.linalg.norm(old[i, :3, 3] - old[parent, :3, 3])
                if np.linalg.norm(delta) > 1e-8:
                    m[:3, 3] = target[parent][:3, 3] + delta / np.linalg.norm(delta) * distance
        target[i] = m
        visiting.remove(i)
        return m
    changes = np.array([build(i) @ np.linalg.inv(old[i]) for i in range(len(bones))])
    updates = []
    for mesh in skin.meshes:
        joints, weights = np.asarray(mesh.joints), np.asarray(mesh.weights)
        valid = (joints >= 0) & (joints < len(bones)) & (weights > 0)
        w = np.where(valid, weights, 0)
        total = w.sum(axis=1)
        w = np.divide(w, total[:, None], out=np.zeros_like(w, dtype=float), where=total[:, None] > 0)
        blended = np.einsum('nk,nkij->nij', w, changes[np.clip(joints, 0, len(bones)-1)])
        blended[total == 0] = np.eye(4)
        if np.any(abs(np.linalg.det(blended[:, :3, :3])) < 1e-8):
            raise ValueError('Cannot prepare actor: singular weighted bind deformation in ' + mesh.name)
        positions = np.einsum('nij,nj->ni', blended[:, :3, :3], mesh.positions) + blended[:, :3, 3]
        normals = mesh.normals
        if normals is not None:
            normals = np.einsum('ni,nij->nj', normals, np.linalg.inv(blended[:, :3, :3]))
            normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
        updates.append((mesh, positions, normals))
    for mesh, positions, normals in updates:
        mesh.positions, mesh.normals = positions, normals
    for i, b in enumerate(bones):
        b.frame, b.head = target[i][:3, :3].copy(), target[i][:3, 3].copy()
    return []
