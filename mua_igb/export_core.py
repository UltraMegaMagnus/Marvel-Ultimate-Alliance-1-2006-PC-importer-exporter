"""Blender-independent MUA1 (PC, v8) .igb writers: skins, static models, animations."""
import os
import numpy as np
from .playback import PLAY_MODE

from . import pose_math as P
from . import dxt
from .template import Builder, Template, RefMem
from .igb import Obj

NO_BM_BONES = {"Bip01 Footsteps", "Motion"}     # bones that are not skinned (no inverse joint matrix)
MAX_PALETTE = 8                                  # blend-matrix palette limit per geometry piece
CULL_BACK, CULL_FRONT = 0, 1


# ----------------------------------------------------------------------- data model
class XBone:
    def __init__(self, name, parent, head, frame):
        self.name, self.parent = name, parent          # parent: index into XSkin.bones or -1
        self.head = np.asarray(head, float)            # file units
        self.frame = np.asarray(frame, float)          # column-convention world rotation of the joint frame


class XTex:
    """rgba: float32 (h, w, 4) top row first, in true colours (no R/B swap applied)."""
    def __init__(self, name, rgba, kind="dxt1", no_swap=False):
        self.name, self.rgba, self.kind, self.no_swap = name, np.asarray(rgba, np.float32), kind, no_swap


class XMat:
    def __init__(self, name):
        self.name = name
        self.blend = 0                                 # MarvelStandart BlendType 0..3
        self.lighting = True
        self.shininess = 0.0
        self.diffuse = (1.0, 1.0, 1.0, 1.0)
        self.ambient = (1.0, 1.0, 1.0, 1.0)
        self.specular = (0.0, 0.0, 0.0, 0.0)
        self.emission = (0.0, 0.0, 0.0, 0.0)
        self.reflectance = (1.0, 0.0, 0.0, 0.0)
        self.transparent = False
        self.diffuse_tex = self.normal_tex = self.specular_tex = self.emissive_tex = None
        self.reflection_faces = None                   # list of 6 XTex (+X,-X,+Y,-Y,+Z,-Z) or None
        self.reflection_sphere = None
        self.reflection_mask = None
        self.uv_anim = None                            # {'times': s, 'trans': (n,3) file units, 'type': 0..3}


class XMesh:
    def __init__(self, name, mat, positions, normals, uv, faces, joints, weights):
        self.name, self.mat = name, mat
        self.positions, self.normals, self.uv = positions, normals, uv      # uv in file convention (v down)
        self.faces, self.joints, self.weights = faces, joints, weights      # joints: (n,4) XSkin bone idx, -1 unused
        self.cull = "DEFAULT"                          # DEFAULT / DISABLE / BACK / FRONT
        self.flags = 0
        self.props = {}                                # extra string properties


class XSkin:
    def __init__(self, name):
        self.name, self.bones, self.meshes = name, [], []
        self.shift = np.zeros(3)                       # added to every vertex (pelvis-height workaround)


# ------------------------------------------------------------------------ helpers
def tangent_frame(pos, nrm, uv, faces):
    """Per-vertex tangent (dP/du) and binormal (dP/dv), orthogonalised against the normal."""
    T = np.zeros_like(pos, np.float64)
    Bn = np.zeros_like(pos, np.float64)
    a, b, c = faces[:, 0], faces[:, 1], faces[:, 2]
    e1, e2 = pos[b] - pos[a], pos[c] - pos[a]
    du1, dv1 = uv[b, 0] - uv[a, 0], uv[b, 1] - uv[a, 1]
    du2, dv2 = uv[c, 0] - uv[a, 0], uv[c, 1] - uv[a, 1]
    r = du1 * dv2 - du2 * dv1
    ok = np.abs(r) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, r, 1.0), 0.0)[:, None]
    tf = (e1 * dv2[:, None] - e2 * dv1[:, None]) * inv
    bf = (e2 * du1[:, None] - e1 * du2[:, None]) * inv
    for k in (a, b, c):
        np.add.at(T, k, tf)
        np.add.at(Bn, k, bf)
    n = nrm.astype(np.float64)
    T = T - n * (T * n).sum(1, keepdims=True)
    Bn = Bn - n * (Bn * n).sum(1, keepdims=True)
    for v, fb in ((T, (1.0, 0.0, 0.0)), (Bn, (0.0, 1.0, 0.0))):
        ln = np.linalg.norm(v, axis=1, keepdims=True)
        bad = ln[:, 0] < 1e-9
        v[bad] = fb
        ln[bad] = 1.0
        v /= ln
    return T.astype(np.float32), Bn.astype(np.float32)


def rest_local(bones):
    """Per bone: (local rotation column matrix, local translation in the parent's joint frame)."""
    out = []
    for b in bones:
        if b.parent >= 0:
            p = bones[b.parent]
            out.append((p.frame.T @ b.frame, p.frame.T @ (b.head - p.head)))
        else:
            out.append((b.frame.copy(), b.head.copy()))
    return out


def rot_to_quat(A_col):
    """Alchemy quaternion (x, y, z, w) for a column-convention local rotation (Rc(q) = A_col^T)."""
    w = P.mats_to_quat_wxyz(np.array([A_col.T]))[0]
    return (float(w[1]), float(w[2]), float(w[3]), float(w[0]))


def split_pieces(mesh, bm_of, max_bones=MAX_PALETTE):
    """Group triangles so that every group touches at most `max_bones` skinned bones.
    Returns [(palette(list of bm idx), tri index array)] and the (possibly pruned) joints/weights."""
    joints, weights = mesh.joints.copy(), mesh.weights.astype(np.float64).copy()
    nv = len(joints)
    bmj = np.full(joints.shape, -1, np.int64)
    for k in range(4):
        for i, bm in bm_of.items():
            bmj[joints[:, k] == i, k] = bm
    weights[bmj < 0] = 0.0
    bmj[weights <= 1e-6] = -1
    weights[bmj < 0] = 0.0
    faces = mesh.faces

    def tri_sets():
        return [set(int(x) for x in bmj[f].ravel() if x >= 0) for f in faces]

    sets = tri_sets()
    for limit in (4, 3, 2, 1):                        # prune influences until every triangle fits
        bad = [i for i, s in enumerate(sets) if len(s) > max_bones]
        if not bad:
            break
        verts = np.unique(faces[bad].ravel())
        for v in verts:
            idx = np.argsort(-weights[v])[:limit]
            keep = np.zeros(4, bool)
            keep[idx] = True
            weights[v, ~keep] = 0.0
            bmj[v, ~keep] = -1
        sets = tri_sets()
    s = weights.sum(1, keepdims=True)
    weights = np.divide(weights, s, out=np.zeros_like(weights), where=s > 0)
    order = sorted(range(len(faces)), key=lambda t: tuple(sorted(sets[t])))
    pieces = []                                       # [set, [tri ids]]
    for t in order:
        st = sets[t]
        best, best_cost = None, None
        for p in pieces:
            u = len(p[0] | st)
            if u <= max_bones:
                cost = u - len(p[0])
                if best is None or cost < best_cost:
                    best, best_cost = p, cost
        if best is None:
            best = [set(), []]
            pieces.append(best)
        best[0] |= st
        best[1].append(t)
    out = []
    for pset, tris in pieces:
        pal = sorted(pset) or [0]
        out.append((pal, np.array(tris, np.int64)))
    return out, bmj, weights



def _weld(arrays):
    """Merge identical vertices. arrays: list of (n,k) float arrays. Returns (unique index per vertex, first-occurrence idx)."""
    key = np.ascontiguousarray(np.concatenate([a.reshape(len(a), -1).astype(np.float32) for a in arrays], axis=1))
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return inv.reshape(-1), first


def build_geometry(B, name, pos, nrm, uv, bin_, tan, ntris, flags=0, w=None, ji=None, indexed=False,
                   with_normals=True, with_uv=True, colors=None):
    """igGeometry with the vertex streams of a Max/Alchemy export. All arrays are per corner (3 per triangle)."""
    index = None
    if indexed:
        parts = [pos, nrm, uv, bin_, tan] + ([w, ji] if w is not None else [])
        if colors is not None:
            parts.append(colors)
        inv, first = _weld(parts)
        if len(first) <= 65535:
            index = inv.astype(np.uint16)
            pos, nrm, uv, bin_, tan = pos[first], nrm[first], uv[first], bin_[first], tan[first]
            if colors is not None:
                colors = colors[first]
            if w is not None:
                w, ji = w[first], ji[first]
    streams = []
    spec = [(1, "igVec3fList", pos, "<f4", 1)]
    if colors is not None:
        packed = np.rint(np.clip(colors, 0, 1)*255).astype("u1")
        spec.append((2, "igVec4ucList", packed, "u1", 1))
    if with_normals:
        spec.append((3, "igVec3fList", nrm.astype(np.float32), "<f4", 1))
    if with_uv:
        spec.append((4, "igVec2fList", uv.astype(np.float32), "<f4", 1))
    if w is not None:
        spec += [(5, "igFloatList", w, "<f4", 4), (6, "igUnsignedCharList", ji, "u1", 4)]
    if with_normals and with_uv:                     # tangent frame only exists with normals and UVs
        spec += [(7, "igVec3fList", bin_, "<f4", 1), (8, "igVec3fList", tan, "<f4", 1)]
    for usage, cls, arr, fmt, comps in spec:
        streams.append(B.obj("igVertexData", {2: "", 3: B.numlist(cls, arr, fmt), 4: usage, 6: comps}))
    va = B.obj("igVertexArray2", {2: "", 3: B.objlist("igObjectList", streams), 4: B.obj("igVertexStream")})
    ia = None
    if index is not None:
        ia = B.obj("igIndexArray", {2: B.mem("igIndexArray", 2, index.tobytes()), 3: int(len(index))})
    ga = B.obj("igGeometryAttr2", {2: 0, 4: va, 5: ia, 6: None, 7: 3, 8: ntris})
    bound = B.obj('igAABox', {2:tuple(np.min(pos,axis=0)),3:tuple(np.max(pos,axis=0))})
    return B.obj("igGeometry", {2: name, 3: bound, 5: flags, 7: B.objlist("igNodeList", []),
                                8: B.objlist("igAttrList", [ga])})


def uv_transform_node(B, mat, child):
    """igTransform carrying a keyframed texture-matrix (UV scroll) above `child`."""
    ua = mat.uv_anim
    times = np.asarray(ua["times"], np.float64)
    trans = np.asarray(ua["trans"], np.float32)
    n = len(times)
    ticks = np.rint(times * 1e9).astype(np.int64)
    M = np.eye(4)
    M[3, :3] = trans[0]
    seq = B.obj("igTransformSequence1_5", {
        2: B.numlist("igVec3fList", trans, "<f4"),
        3: B.numlist("igQuaternionfList", np.tile([0, 0, 0, 1.0], (n, 1)).astype(np.float32), "<f4"),
        4: B.numlist('igVec3fList', np.ones((n,3)), '<f4'),
        8: int(ua.get("type", 0)), 11: B.numlist("igLongList", ticks, "<i8"),
        15: 11, 16:bytes((1,3,1)), 18: int(ticks[-1])})
    # Texture transforms do not move geometry. Keep their descendant bounds.
    bound = subtree_bound(B, child)
    return B.obj("igTransform", {2: "", 3: bound, 5:2, 7: B.objlist("igNodeList", [child]),
                                 8: tuple(float(x) for x in M.ravel()), 9:9, 11: seq})


def subtree_bound(B, node):
    bound = node.get(3)
    if isinstance(bound, Obj) and bound.type == 'igAABox':
        return bound
    children = node.get(7)
    refs = getattr(children.get(4), 'refs', ()) if isinstance(children, Obj) else ()
    boxes = [subtree_bound(B, c) for c in refs]
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    bound = B.obj('igAABox', {2:tuple(np.min([b.get(2) for b in boxes],axis=0)),
                            3:tuple(np.max([b.get(3) for b in boxes],axis=0))})
    node.fields[3] = bound
    return bound


# ------------------------------------------------------------------- shader pieces
def make_image(B, tex, cache, pfmt=None):
    key = id(tex)
    if key in cache:
        return cache[key]
    rgba = tex.rgba
    h, w = rgba.shape[:2]
    swap = bool(getattr(B, "swap_rb", False)) and not tex.no_swap
    if tex.kind == "rgba8":
        src = rgba[..., [2, 1, 0, 3]] if swap else rgba
        data = (np.clip(src, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes()
        pf, sub, bpr = 7, 4, w * 4
    else:
        data = dxt.encode(rgba, tex.kind, swap_rb=swap)
        pf = 14 if tex.kind == "dxt1" else 16
        sub = 1 if tex.kind == "dxt1" else 2
        bpr = (w // 4) * (8 if tex.kind == "dxt1" else 16)
    mem = B.mem("igImage", 13, data)
    img = B.obj("igImage", {2: w, 3: h, 7: sub, 8: sub, 9: sub, 10: sub, 11: pf, 12: len(data), 13: mem,
                            14: None, 19: bpr, 22: tex.name})
    mips = B.objlist("igImageMipMapList", [])
    ta = B.obj("igTextureAttr", {12: img, 16: mips})
    cache[key] = (img, ta)
    return cache[key]


def material_attrs(B, mat, cache, images):
    """igAttr list for one MarvelStandart material, in the shader's attribute order."""
    attrs = []
    depth = getattr(mat,"depth_write",None)
    if depth is not None:
        attr = B.obj("igDepthWriteStateAttr")
        B.set_kind(attr,"Bool",int(depth))
        attrs.append(attr)
    if getattr(mat, "uv_anim", None) is not None or getattr(mat,"texture_matrix_present",False):
        tm = B.obj("igTextureMatrixStateAttr")
        B.set_kind(tm, "Bool", int(getattr(mat,"texture_matrix_enabled",True)))
        B.set_kind(tm, "Int", 0)
        attrs.append(tm)
    if not mat.lighting:
        a = B.obj("igLightingStateAttr")
        B.set_kind(a, "Bool", 0)
        attrs.append(a)
    col = B.obj("igColorAttr")
    B.set_kind(col, "Vec4f", tuple(float(x) for x in mat.diffuse))
    attrs.append(col)
    m = B.obj("igMaterialAttr")
    B.set_kind(m, "Float", float(mat.shininess))
    for i, v in enumerate((mat.diffuse, mat.ambient, mat.emission, mat.specular)):
        B.set_kind(m, "Vec4f", tuple(float(x) for x in v), i)
    attrs.append(m)
    if mat.blend:
        src, dst = {1: (4, 5), 2: (4, 1), 3: (1, 5)}[mat.blend]
        bf = B.obj("igBlendFunctionAttr")
        B.set_kind(bf, "Enum", src, 0)
        B.set_kind(bf, "Enum", dst, 1)
        bs = B.obj("igBlendStateAttr")
        B.set_kind(bs, "Bool", 1)
        attrs += [bf, bs]
    if getattr(mat, "alpha_test", False):
        af, ast = B.obj("igAlphaFunctionAttr"), B.obj("igAlphaStateAttr")
        B.set_kind(af, "Enum", mat.alpha_func)
        B.set_kind(af, "Float", mat.alpha_ref)
        B.set_kind(ast, "Bool", 1)
        attrs.extend([af, ast])
    if getattr(mat,"shader_parameters_enabled",bool(mat.reflection_faces or mat.reflection_sphere is not None)):
        vec = B.obj("igShaderConstantVector")
        B.set_kind(vec, "String", "material.reflectance")
        B.set_kind(vec, "Bool", 1)
        B.set_kind(vec, "Vec4f", tuple(float(x) for x in mat.reflectance))
        tr = B.obj("igShaderConstantBool")
        B.set_kind(tr, "String", "material.transparent")
        B.set_kind(tr, "Bool", 1)
        B.set_kind(tr, "Int", 1 if mat.transparent else 0, 1)
        lst = B.objlist("igShaderConstantDataList", [vec, tr])
        sp = B.obj("igShaderParametersAttr", {4: lst})
        attrs.append(sp)

    def bind(unit, ta):
        b = B.obj("igTextureBindAttr", {2: unit, 4: ta, 5: unit})
        s = B.obj("igTextureStateAttr", {2: 0, 4: 1, 5: unit})
        attrs.extend([b, s])

    for unit, tex in ((0, mat.diffuse_tex), (1, mat.normal_tex), (2, mat.specular_tex)):
        if tex is not None:
            bind(unit, make_image(B, tex, cache)[1])
            images.append(tex)
    if mat.reflection_faces:
        faces = [make_image(B, t, cache) for t in mat.reflection_faces]
        sphere = make_image(B, _sphere_from_faces(mat.reflection_faces), cache)
        cube = B.obj("igTextureCubeAttr", {12: sphere[0], 16: B.objlist("igImageMipMapList", []),
                                           19: [faces[i][0] for i in (1,0,3,2,5,4)], 20: 1})
        bind(3, cube)
    if mat.reflection_sphere is not None:
        sphere = make_image(B, mat.reflection_sphere, cache)
        cube = B.obj("igTextureCubeAttr", {12: sphere[0], 16: B.objlist("igImageMipMapList", []), 19: [None]*6, 20: 2})
        bind(3, cube)
    if mat.reflection_mask is not None:
        bind(4, make_image(B, mat.reflection_mask, cache)[1])
        images.append(mat.reflection_mask)
    if mat.emissive_tex is not None:
        bind(5, make_image(B, mat.emissive_tex, cache)[1])
        images.append(mat.emissive_tex)
    if getattr(B, "generate_global_color", True):
        gcs = B.obj("igGlobalColorStateAttr")
        B.set_kind(gcs, "Bool", 1)
        attrs.append(gcs)
    return attrs


def _sphere_from_faces(faces, size=128):
    """Sphere-map image (the game's fallback 'sphereImage') derived from the six cube faces."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float64)
    x = (xs + 0.5) / size * 2 - 1
    y = 1 - (ys + 0.5) / size * 2
    r2 = x * x + y * y
    z = np.sqrt(np.clip(1 - r2, 0, 1))
    n = np.stack([x, y, z], -1)
    d = 2 * n[..., 2:3] * n - np.array([0, 0, 1.0])            # reflect the view vector (0,0,1) around n
    ax = np.abs(d)
    face = np.argmax(ax, -1)
    sign = np.take_along_axis(d, face[..., None], -1)[..., 0] < 0
    idx = face * 2 + sign                                       # 0:+X 1:-X 2:+Y 3:-Y 4:+Z 5:-Z
    out = np.zeros((size, size, 4), np.float32)
    out[..., 3] = 1.0
    for i, t in enumerate(faces):
        sel = idx == i
        if not sel.any():
            continue
        img = t.rgba
        h, w = img.shape[:2]
        dd = d[sel]
        m = np.abs(dd).max(1, keepdims=True)
        u = (dd / m)
        a = {0: (-u[:, 2], -u[:, 1]), 1: (u[:, 2], -u[:, 1]), 2: (u[:, 0], u[:, 2]), 3: (u[:, 0], -u[:, 2]),
             4: (u[:, 0], -u[:, 1]), 5: (-u[:, 0], -u[:, 1])}[i]
        px = np.clip(((a[0] + 1) / 2 * (w - 1)).round().astype(int), 0, w - 1)
        py = np.clip(((a[1] + 1) / 2 * (h - 1)).round().astype(int), 0, h - 1)
        out[sel] = img[py, px]
    return XTex("sphereImage", out, "rgba8")


# ------------------------------------------------------------------------- skin

def make_skeleton(B, name, bones, trans_override=None, deforming=()):
    """igSkeleton + bone infos for `bones` (dummy root first). Returns (skeleton, bm_of, bm_count, rest_local)."""
    N = len(bones)
    bm_of, bm_count = {}, 0
    for i, b in enumerate(bones):
        if b.name not in NO_BM_BONES or b.name in deforming or not np.allclose(b.frame, np.eye(3), atol=1e-6):
            bm_of[i] = bm_count
            bm_count += 1
    rl = rest_local(bones)
    trans = np.zeros((N + 1, 3), np.float32)
    for i in range(N):
        trans[i + 1] = rl[i][1]
    if trans_override:
        for i, t in trans_override.items():
            trans[i + 1] = t
    infos = [B.obj("igSkeletonBoneInfo", {2: "Unnamed bone", 3: -1, 4: -1, 5: 64})]
    for i, b in enumerate(bones):
        infos.append(B.obj("igSkeletonBoneInfo", {2: b.name, 3: (b.parent + 1) if b.parent >= 0 else 0,
                                                   4: bm_of.get(i, -1), 5: 0}))
    info_list = B.objlist("igSkeletonBoneInfoList", infos)
    inv = np.zeros((bm_count, 16), np.float32)
    for i, bm in bm_of.items():
        W = np.eye(4)
        W[:3, :3] = bones[i].frame.T
        W[3, :3] = bones[i].head
        inv[bm] = np.linalg.inv(W).ravel()
    skel = B.obj("igSkeleton", {2: name, 3: B.mem("igSkeleton", 3, trans.tobytes()), 4: info_list,
                                5: B.mem("igSkeleton", 5, inv.tobytes()), 6: bm_count})
    return skel, bm_of, bm_count, rl

def build_skin_file(path, skin, swap_rb=False):
    """Write a MUA1 PC skin (.igb) following the layout of a Max/Alchemy 5 export.
    Colours are written as they are in the images unless swap_rb is set."""
    B = Builder()
    B.swap_rb = swap_rb
    B.generate_global_color = getattr(skin, "generate_global_color", True)
    bones = skin.bones
    N = len(bones)
    # ---- skeleton -----------------------------------------------------------
    deforming = {bones[int(j)].name for mesh in skin.meshes if mesh.joints is not None
                 for j in mesh.joints[np.asarray(mesh.weights) > 1e-6] if 0 <= j < len(bones)}
    skel, bm_of, bm_count, rl = make_skeleton(B, "defaultSkeleton", bones, deforming=deforming)
    # ---- animation scaffolding ---------------------------------------------
    tracks, states = [], []
    quats = [rot_to_quat(rl[i][0]) for i in range(N)]
    live_animation = getattr(skin, "animation", None)
    translation_words = np.zeros((N+31)//32, np.uint32)
    for i, b in enumerate(bones):
        source = None
        if live_animation is not None:
            q,t = live_animation.tracks[b.name]
            times = np.rint(np.arange(live_animation.n)/live_animation.rate*1e9).astype(np.int64)
            fields = {3:B.numlist("igQuaternionfList",q,"<f4"),8:PLAY_MODE[live_animation.playback_type],11:B.numlist("igLongList",times,"<i8"),15:2,16:bytes((1,3,1)),18:int(round(live_animation.duration*1e9))}
            if t is not None:
                fields[2] = B.numlist("igVec3fList",t,"<f4")
                fields[15] |= 1
                translation_words[i//32] |= np.uint32(1 << (i%32))
            if b.name in live_animation.scales:
                fields[4] = B.numlist("igVec3fList",live_animation.scales[b.name],"<f4")
                fields[15] |= 8
            source = B.obj("igTransformSequence1_5",fields)
        tracks.append(B.obj("igAnimationTrack", {2: b.name, 3: source, 4: quats[i], 5: (0.0, 0.0, 0.0)}))
    track_list = B.objlist("igAnimationTrackList", tracks)
    bind_map = np.array([-1] + list(range(N)), np.int32)
    binding = B.obj("igAnimationBinding", {2: skel, 3: B.mem("igAnimationBinding", 3, bind_map.tobytes()), 4: N,
                                           5: None, 6: None})
    anim = B.obj("igAnimation", {2: "defaultAnimation", 3: 0, 4: B.objlist("igAnimationBindingList", [binding]),
                                 5: track_list, 6: B.objlist("igAnimationTransitionDefinitionList", []), 10: None})
    if live_animation is not None:
        anim.fields[2] = live_animation.name
        anim.fields[9] = int(round(live_animation.duration*1e9))
        anim.fields[10] = B.obj("igBitMask",{2:len(translation_words),3:len(translation_words),4:B.mem("igBitMask",4,translation_words.tobytes()),5:N})
    anim_list = B.objlist("igAnimationList", [anim])
    state = B.obj("igAnimationState", {2: anim})
    if live_animation is not None:
        # Native Status::kPlaying=0, TransitionMode::kNone=4; full blend weight.
        state.fields.update({4:4,5:0,8:1.0,14:1.0,15:0.0})
    state_list = B.objlist("igAnimationStateList", [state])
    lists = [B.objlist("igAnimationCombinerBoneInfoList", [])]
    for i in range(N):
        ci = B.obj("igAnimationCombinerBoneInfo", {2: state, 3: None, 4: quats[i],
                                                   5: tuple(float(x) for x in rl[i][1]), 6: 0, 7: 0, 9: -1})
        if live_animation is not None:
            ci.fields[3] = tracks[i].fields[3]
            ci.fields[7] = int(live_animation.tracks[bones[i].name][1] is not None)
        lists.append(B.objlist("igAnimationCombinerBoneInfoList", [ci]))
    ll = B.objlist("igAnimationCombinerBoneInfoListList", lists)
    zi = B.numlist("igIntList", np.zeros(N + 1, np.int32), "<i4")
    m8 = np.zeros((N + 1, 16), np.float32)
    m8[0, 15] = 1.0
    combiner = B.obj("igAnimationCombiner", {
        2: "", 3: skel, 4: ll, 5: zi, 6: state_list,
        7: B.mem("igAnimationCombiner", 7, np.zeros((N + 1, 4), np.float32).tobytes()),
        8: B.mem("igAnimationCombiner", 8, m8.tobytes()),
        12: B.mem("igAnimationCombiner", 12, np.zeros((N + 1, 16), np.float32).tobytes()),
        13: B.mem("igAnimationCombiner", 13, np.zeros((bm_count, 16), np.float32).tobytes())})
    # ---- geometry -------------------------------------------------------------
    outer_nodes, all_pos = [], []
    segment_nodes = {}
    images, cache, attr_cache = [], {}, {}
    full_palette = np.arange(bm_count, dtype=np.int32)
    for mesh in skin.meshes:
        pos = mesh.positions.astype(np.float32) + skin.shift.astype(np.float32)
        all_pos.append(pos)
        T, Bn = tangent_frame(pos, mesh.normals, mesh.uv, mesh.faces)
        pieces, bmj, weights = split_pieces(mesh, bm_of)
        if getattr(mesh, "use_material", True):
            m_attrs = attr_cache.get(id(mesh.mat))
            if m_attrs is None:
                m_attrs = attr_cache[id(mesh.mat)] = material_attrs(B, mesh.mat, cache, images)
        else:
            m_attrs = []
        obj_attrs = []
        if mesh.cull != "DEFAULT":                       # "Default" writes no cull attribute (as the Max exporter)
            cull = B.obj("igCullFaceAttr")
            if mesh.cull == "DISABLE":
                B.set_kind(cull, "Bool", 0)
            else:
                B.set_kind(cull, "Bool", 1)
                B.set_kind(cull, "Enum", CULL_FRONT if mesh.cull == "FRONT" else CULL_BACK)
            obj_attrs.append(cull)
        vbs = B.obj("igVertexBlendStateAttr")
        B.set_kind(vbs, "Bool", 1)
        gcs = None
        if B.generate_global_color:
            gcs = B.obj("igGlobalColorStateAttr")
            B.set_kind(gcs, "Bool", 1)
        inner = []
        for pal, tris in pieces:
            f = mesh.faces[tris].reshape(-1)                       # non-indexed: 3 corners per triangle
            slot_of = {bm: s for s, bm in enumerate(pal)}
            ji = np.zeros((len(f), 4), np.uint8)
            for k in range(4):
                col = bmj[f, k]
                ji[:, k] = [slot_of.get(int(x), 0) if x >= 0 else 0 for x in col]
            w = weights[f].astype(np.float32)
            geo = build_geometry(B, mesh.name, pos[f], mesh.normals[f], mesh.uv[f], Bn[f], T[f], len(tris),
                                 mesh.flags, w, ji, getattr(mesh, "indexed", False),
                                 getattr(mesh, "use_normals", True), getattr(mesh, "use_uv", True),
                                 mesh.colors[f] if getattr(mesh, "colors", None) is not None else None)
            pal_list = B.numlist("igIntList", np.array(pal, np.int32), "<i4")
            bms = B.obj("igBlendMatrixSelect", {2: "Bip01", 3: None, 5: 0, 7: B.objlist("igNodeList", [geo]),
                                                8: B.objlist("igAttrList", []), 10: pal_list})
            inner.append(bms)
        obj_set = B.obj("igAttrSet", {2: mesh.name, 3: None, 5: 0, 7: B.objlist("igNodeList", inner),
                                      8: B.objlist("igAttrList", obj_attrs + [vbs] + ([gcs] if B.generate_global_color else []))})
        child = uv_transform_node(B, mesh.mat, obj_set) if mesh.mat.uv_anim is not None else obj_set
        mat_set = B.obj("igAttrSet", {2: mesh.mat.name, 3: None, 5: 0, 7: B.objlist("igNodeList", [child]),
                                      8: B.objlist("igAttrList", m_attrs)})
        outer = B.obj("igBlendMatrixSelect", {2: "Bip01", 3: None, 5: 0, 7: B.objlist("igNodeList", [mat_set]),
                                              8: B.objlist("igAttrList", []),
                                              10: B.numlist("igIntList", full_palette, "<i4")})
        if getattr(mesh, "segment", False):
            segment_nodes.setdefault(mesh.segment_name, []).append(outer)
        else:
            outer_nodes.append(outer)
    for segment_name, children in segment_nodes.items():
        outer_nodes.append(B.obj("igSegment", {2: segment_name, 3: None, 5: 0, 7: B.objlist("igNodeList", children)}))
    group = B.obj("igGroup", {2: "Bip01", 3: None, 5: 0, 7: B.objlist("igNodeList", outer_nodes)})
    igskin = B.obj("igSkin", {2: skin.name, 3: group, 4: None})
    # ---- actor wrappers -------------------------------------------------------
    allp = np.concatenate(all_pos) if all_pos else np.zeros((1, 3), np.float32)
    lo, hi = tuple(float(x) for x in allp.min(0)), tuple(float(x) for x in allp.max(0))
    appearance = B.obj("igAppearance", {2: "", 3: igskin, 4: B.objlist("igSkinList", []),
                                        5: B.objlist("igModelViewMatrixBoneSelectList", []),
                                        6: B.objlist("igStringObjList", []), 7: B.objlist("igNodeList", [])})
    actor = B.obj("igActor", {2: "Bip01", 3: B.obj("igAABox", {2: lo, 3: hi}), 5: 0, 7: B.objlist("igNodeList", []),
                              8: combiner, 9: B.mem("igActor", 9, np.zeros((N + 1, 16), np.float32).tobytes()),
                              10: B.mem("igActor", 10, np.zeros((bm_count, 16), np.float32).tobytes()),
                              11: appearance, 12: None, 13: B.objlist("igAnimationModifierList", [])})
    if live_animation is not None:
        actor.fields[5] = 2
    db = B.obj("igAnimationDatabase", {2: "defaultAnimDB", 4: 1, 5: B.objlist("igSkeletonList", [skel]),
                                       6: anim_list, 7: B.objlist("igSkinList", [igskin]),
                                       8: B.objlist("igAppearanceList", []), 9: B.objlist("igAnimationCombinerList", [])})
    actor_info = B.obj("igActorInfo", {2: "Bip01", 4: 1, 5: None, 6: B.objlist("igActorList", [actor]), 7: db,
                                       8: B.objlist("igAnimationCombinerList", [combiner]),
                                       9: B.objlist("igAppearanceList", [appearance])})
    hashed, seen_names = [], set()
    segment_names = {m_.name for m_ in skin.meshes if getattr(m_, "segment", False)}
    for mesh in skin.meshes:
        if mesh.name in seen_names or mesh.name in segment_names:
            continue                                  # a segment node has no user-info wrapper (see TEST.igb)
        seen_names.add(mesh.name)
        grp = B.obj("igGroup", {2: mesh.name, 3: None, 5: 0, 7: B.objlist("igNodeList", [])})
        props = []
        for k, v in [("LastPose", "undefined"), ("MapChannel:1", "UV0")] + list(mesh.props.items()):
            props.append(B.obj("igProperty", {2: B.obj("igStringKey", {2: k}), 3: B.obj("igStringValue", {2: v})}))
        hashed.append(B.obj("igHashedUserInfo", {2: mesh.name, 3: None, 5: 0, 7: B.objlist("igNodeList", [grp]),
                                                 8: B.objlist("igPropertyList", props)}))
    scene_root = B.obj("igAttrSet", {2: "Scene Root", 3: B.obj("igAABox", {2: lo, 3: hi}), 5: 0,
                                     7: B.objlist("igNodeList", [actor] + hashed + helper_nodes(B, skin)), 8: B.objlist("igAttrList", []), 9: 0})
    tex_attrs = [v[1] for v in cache.values() if v[1].meta.name == "igTextureAttr"]
    scene = B.obj("igSceneInfo", {2: "Scene Graph", 4: 1, 5: scene_root, 6: B.objlist("igTextureList", tex_attrs),
                                  7: B.objlist("igGraphPathList", []), 8: 0, 9: 0, 10: (0.0, 0.0, 1.0),
                                  11: B.objlist("igNodeList", [])})
    info = B.objlist("igInfoList", [actor_info, db, scene])
    B.finish(path, info)
    return {"bones": N, "bm": bm_count, "pieces": sum(1 for _ in B.items if isinstance(_, Obj) and _.meta.name == "igGeometry")}


# ------------------------------------------------------------------- animation
class XAnim:
    """tracks: {bone name: (quats (n,4) xyzw in Alchemy convention, trans (n,3) file units or None)}."""
    def __init__(self, name, rate, n):
        self.name, self.rate, self.n = name, float(rate), int(n)
        self.tracks = {}
        self.scales = {}
        self.playback_type = "REPEAT"

    @property
    def duration(self):
        return getattr(self, "duration_override", (self.n - 1) / float(self.rate))


def build_anim_file(path, anim, bones, skeleton_name=None, fmt="SEQUENCE"):
    """Write a single-animation .igb.

    fmt "SEQUENCE": every track is an igTransformSequence1_5 keyed on each sample, first frame included
                    (what the Alchemy optimizers such as Extract Motion expect to find).
    fmt "ENBAYA":   compressed like the game's own files (Enbaya streams, plain keyframes for Motion).
    The igAnimation and database use the Blender Action name."""
    from . import enbaya_enc
    if fmt == "ENBAYA":
        # Enbaya stores an integer sample rate. Preserve the requested duration
        # instead of truncating a fractional rate in its binary header.
        duration = anim.duration
        rate = max(1, int(np.ceil(anim.rate - 1e-7)))
        count = int(np.ceil(duration * rate - 1e-5)) + 1
        source_times = np.arange(anim.n) / anim.rate
        target_times = np.arange(count) / rate
        resampled = XAnim(anim.name, rate, count)
        resampled.duration_override = duration
        resampled.playback_type = anim.playback_type
        resampled.scales = anim.scales
        if hasattr(anim, "motion_velocity"):
            resampled.motion_velocity = anim.motion_velocity
            resampled.motion_offset = getattr(anim, "motion_offset", (0,0,0))
        def interpolate(values, extrapolate):
            if values is None:
                return None
            values = np.asarray(values, np.float64)
            out = np.stack([np.interp(target_times, source_times, values[:, k]) for k in range(values.shape[1])], axis=1)
            if extrapolate and count > 1 and target_times[-1] > duration + 1e-9:
                f = (duration-target_times[-2]) / (target_times[-1]-target_times[-2])
                if f > 1e-8:
                    out[-1] = out[-2] + (values[-1]-out[-2])/f
            return out
        for name, (q, tr) in anim.tracks.items():
            q = np.asarray(q, np.float64).copy()
            for k in range(1,len(q)):
                if np.dot(q[k-1],q[k]) < 0:
                    q[k] *= -1
            resampled.tracks[name] = (interpolate(q, name != "Motion"), interpolate(tr, name != "Motion"))
        anim = resampled
    B = Builder()
    N = len(bones)
    names = [b.name for b in bones]
    special = [n for n in ("Bip01", "Motion") if n in names]
    order = [i for i, n in enumerate(names) if n not in special] + [names.index(n) for n in special]
    track_of = {bi: ti for ti, bi in enumerate(order)}
    T = len(order)
    rl = rest_local(bones)
    n = anim.n
    dur_ns = int(round(anim.duration * 1e9)) + (23 if fmt == "ENBAYA" else 0)
    times_ns = (np.minimum(np.arange(n) / float(anim.rate), anim.duration) * 1e9).astype(np.int64)
    has_trans = set()

    def samples(bi):
        nm = names[bi]
        q, t = anim.tracks.get(nm, (None, None))
        if q is None:
            q = np.tile(np.array(rot_to_quat(rl[bi][0])), (n, 1))
        q = np.asarray(q, np.float64)
        q = q / np.linalg.norm(q, axis=1, keepdims=True)
        for k in range(1, len(q)):                       # keep the quaternion hemisphere continuous
            if np.dot(q[k], q[k - 1]) < 0:
                q[k] = -q[k]
        return q, (None if t is None else np.asarray(t, np.float64))

    def sequence(bi):
        q, t = samples(bi)
        f = {3: B.numlist("igQuaternionfList", q.astype(np.float32), "<f4"), 8: PLAY_MODE[anim.playback_type],
             11: B.numlist("igLongList", times_ns, "<i8"), 15: 3 if t is not None else 2, 18: dur_ns}
        if t is not None:
            f[2] = B.numlist("igVec3fList", t.astype(np.float32), "<f4")
        if names[bi] in anim.scales:
            f[4] = B.numlist("igVec3fList", anim.scales[names[bi]], "<f4")
            f[15] |= 8
            f[16] = bytes((1, 3, 1))
        return B.obj("igTransformSequence1_5", f)

    if fmt == "ENBAYA" and anim.scales:
        raise ValueError("Enbaya cannot preserve scale tracks; choose Raw keyframes")
    src = None
    if fmt == "ENBAYA":
        S = np.zeros((T, n, 7))
        S[:, :, 3] = 1.0
        for ti, bi in enumerate(order):
            if names[bi] == "Motion":
                continue
            q, t = samples(bi)
            S[ti, :, :4] = q
            if t is not None:
                S[ti, :, 4:] = t
        blob = enbaya_enc.encode(S, anim.rate, anim.duration)
        src = B.obj("igEnbayaAnimationSource", {2: B.mem("igEnbayaAnimationSource", 2, blob), 5: PLAY_MODE[anim.playback_type]})
    tracks = []
    for ti, bi in enumerate(order):
        nm = names[bi]
        if fmt == "ENBAYA" and nm != "Motion":
            source = B.obj("igEnbayaTransformSource", {2: ti, 3: src})
            if anim.tracks.get(nm, (None, None))[1] is not None:
                has_trans.add(ti)
        else:
            source = sequence(bi)
            if samples(bi)[1] is not None:
                has_trans.add(ti)
        tracks.append(B.obj("igAnimationTrack", {2: nm, 3: source, 4: getattr(anim, "motion_velocity", (0.0, 0.0, 0.0, 0.0)) if nm == "Motion" else (0.0, 0.0, 0.0, 0.0), 5: getattr(anim, "motion_offset", (0.0, 0.0, 0.0)) if nm == "Motion" else (0.0, 0.0, 0.0)}))
    track_list = B.objlist("igAnimationTrackList", tracks)
    # The animation-level translation mask is required for raw sources too.
    words = np.zeros((T + 31) // 32, np.uint32)
    for ti in has_trans:
        words[ti // 32] |= np.uint32(1 << (ti % 32))
    mask = B.obj("igBitMask", {2: len(words), 3: len(words), 4: B.mem("igBitMask", 4, words.tobytes()), 5: T})
    skel_real, _, _, _ = make_skeleton(B, (skeleton_name or anim.name) + "_skel", bones)
    over = {}                                       # the default skeleton carries the frame-0 pose of the animated roots
    for nm in ("Bip01", "Motion"):
        if nm in names:
            t = anim.tracks.get(nm, (None, None))[1]
            if t is not None:
                over[names.index(nm)] = np.asarray(t, np.float64)[0]
    skel_def, _, _, _ = make_skeleton(B, "defaultSkeleton", bones, over)
    bind_map = np.array([-1] + [track_of[i] for i in range(N)], np.int32)
    bindings = [B.obj("igAnimationBinding", {2: sk, 3: B.mem("igAnimationBinding", 3, bind_map.tobytes()), 4: T,
                                             5: None, 6: None}) for sk in (skel_def, skel_real)]
    animation = B.obj("igAnimation", {2: anim.name, 3: 0, 4: B.objlist("igAnimationBindingList", bindings),
                                      5: track_list, 6: B.objlist("igAnimationTransitionDefinitionList", []),
                                      7: 0, 8: 0, 9: dur_ns, 10: mask})
    db = B.obj("igAnimationDatabase", {2: anim.name, 4: 1,
                                       5: B.objlist("igSkeletonList", [skel_real]),
                                       6: B.objlist("igAnimationList", [animation]),
                                       7: B.objlist("igSkinList", []), 8: B.objlist("igAppearanceList", []),
                                       9: B.objlist("igAnimationCombinerList", [])})
    B.finish(path, B.objlist("igInfoList", [db]))
    return {"format": fmt, "tracks": T, "samples": n, "translation_tracks": len(has_trans)}


# ------------------------------------------------------------------ static model
class XStatic:
    def __init__(self, name):
        self.name, self.meshes = name, []      # XMesh with joints/weights = None


def helper_nodes(B, model):
    # Helpers are exported in world space under the scene root, including those
    # without geometry. Their transforms never depend on mesh vertex groups.
    return [B.obj("igTransform", {2:h["name"],3:None,5:h["flags"],
                7:B.objlist("igNodeList",[]),8:tuple(h["matrix"].ravel()),9:0,11:None})
            for h in getattr(model,"helpers",[])]


def build_static_file(path, model, swap_rb=False):
    """Static (non-skinned) model: scene info -> attr-set -> geometry, like the MUA prop files."""
    B = Builder()
    B.swap_rb = swap_rb
    images, cache, attr_cache = [], {}, {}
    mat_sets, all_pos = [], []
    by_mat = {}
    for mesh in model.meshes:
        by_mat.setdefault(id(mesh.mat), (mesh.mat, []))[1].append(mesh)
    for mat, meshes in by_mat.values():
        attrs = material_attrs(B, mat, cache, images)
        geos = []
        for mesh in meshes:
            pos = (mesh.positions + model.shift).astype(np.float32) if hasattr(model, "shift") else mesh.positions.astype(np.float32)
            all_pos.append(pos)
            nrm = mesh.normals if mesh.normals is not None else np.tile([0, 0, 1.0], (len(pos), 1))
            T, Bn = tangent_frame(pos, nrm, mesh.uv, mesh.faces)
            f = mesh.faces.reshape(-1)
            g_ = build_geometry(B, mesh.name, pos[f], np.asarray(nrm)[f], mesh.uv[f], Bn[f], T[f], len(mesh.faces),
                                mesh.flags, None, None, getattr(mesh, "indexed", False),
                                getattr(mesh, "use_normals", True), getattr(mesh, "use_uv", True),
                                 mesh.colors[f] if getattr(mesh, "colors", None) is not None else None)
            if mesh.cull != "DEFAULT":
                cull = B.obj("igCullFaceAttr")
                if mesh.cull == "DISABLE":
                    B.set_kind(cull, "Bool", 0)
                else:
                    B.set_kind(cull, "Bool", 1)
                    B.set_kind(cull, "Enum", CULL_FRONT if mesh.cull == "FRONT" else CULL_BACK)
                g_ = B.obj("igAttrSet", {2: mesh.name, 3: None, 5: 0, 7: B.objlist("igNodeList", [g_]),
                                         8: B.objlist("igAttrList", [cull])})
            from .embedded_export import wrap
            geos.append(wrap(B, mesh, g_))
        nodes_ = geos
        if mat.uv_anim is not None:             # enabling attr-set -> igTransform (texture matrix) -> geometry
            inner = B.obj("igAttrSet", {2: mat.name, 3: None, 5: 0, 7: B.objlist("igNodeList", geos),
                                        8: B.objlist("igAttrList", [])})
            nodes_ = [uv_transform_node(B, mat, inner)]
        child = B.obj("igAttrSet", {2: mat.name, 3: None, 5: 0, 7: B.objlist("igNodeList", nodes_),
                                    8: B.objlist("igAttrList", attrs)})
        mat_sets.append(child)
    allp = np.concatenate(all_pos) if all_pos else np.zeros((1, 3), np.float32)
    lo, hi = tuple(float(x) for x in allp.min(0)), tuple(float(x) for x in allp.max(0))
    root = B.obj("igAttrSet", {2: "", 3: B.obj("igAABox", {2: lo, 3: hi}), 5: 0,
                               7: B.objlist("igNodeList", mat_sets + helper_nodes(B, model)), 8: B.objlist("igAttrList", []), 9: 0})
    tex_attrs = [v[1] for v in cache.values() if v[1].meta.name == "igTextureAttr"]
    scene = B.obj("igSceneInfo", {2: "Scene Graph", 4: 1, 5: root, 6: B.objlist("igTextureList", tex_attrs),
                                  7: B.objlist("igGraphPathList", []), 8: 0,
                                  9: max((s.get(18,0) for s in B.items if isinstance(s,Obj) and s.type=='igTransformSequence1_5'),default=0), 10: (0.0, 0.0, 1.0),
                                  11: B.objlist("igNodeList", [])})
    infos = [scene]
    if getattr(model, "generate_hull", False):
        from .collision import build
        infos.append(build(B, model.meshes))
    B.finish(path, B.objlist("igInfoList", infos))
    return {"meshes": len(model.meshes), "materials": len(by_mat)}
