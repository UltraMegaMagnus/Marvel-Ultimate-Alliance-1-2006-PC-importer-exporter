"""Extract skeleton / meshes / materials from Alchemy .igb files.

Handles MUA1 (v8, igGeometryAttr2), Alchemy 2.5 / 3ds Max 5 exports (v4, igGeometryAttr1_5)
and X-Men Legends (v6, igGeometryAttr1_5 + strips). Skinned or static.

Pure python + numpy (no bpy). Matrices from the file are Alchemy row-vector matrices; everything
returned here is converted to column-vector convention (Blender / mathutils style).
"""
import struct
import numpy as np

from .igb import IGB, Obj, Mem
from . import dxt

# igVertexData usage enum (igGeometryAttr2)
U_POS, U_NORMAL, U_UV, U_WEIGHT, U_BONEIDX, U_BINORMAL, U_TANGENT = 1, 3, 4, 5, 6, 7, 8
# igVertexArray1_1 external stream slots + vertex-format bits
EXT_POS, EXT_NORMAL, EXT_COLOR, EXT_UV = 0, 1, 2, 11
VF_BW_MASK, VF_BW_SHIFT, VF_BI_MASK, VF_BI_SHIFT = 0xF0, 4, 0xF00, 8
PRIM_TRI_LIST, PRIM_TRI_STRIP = 3, 4


def R(g, o, slot):
    """Resolve an ObjectRef slot (None if not an object ref / unset)."""
    if o is None or slot not in o.fields or o.kinds[slot] != "ObjectRef":
        return None
    return g.ref(o.fields[slot])


def refs(g, o):
    """All non-null ObjectRef targets of an object, in slot order."""
    out = []
    for s in sorted(o.fields):
        if o.kinds[s] == "ObjectRef" and o.fields[s] >= 0:
            t = g.ref(o.fields[s])
            if t is not None and hasattr(t, "fields") or isinstance(t, Mem):
                out.append(t)
    return out


def ref_of(g, o, cls):
    for t in refs(g, o):
        if isinstance(t, Obj) and t.is_a(cls):
            return t
    return None


class Bone:
    def __init__(self, index, name, parent, bm, trans):
        self.index, self.name, self.parent, self.bm = index, name, parent, bm
        self.trans = trans            # local translation (parent joint frame)
        self.rot_world = np.eye(3)    # column-convention world rotation (joint frame)
        self.pos_world = np.zeros(3)
        self.rot_local = np.eye(3)    # column-convention local rest rotation
        self.children = []
        self.added = False


class Texture:
    def __init__(self, unit, name, w, h, kind, rgba):
        self.unit, self.name, self.w, self.h, self.kind = unit, name, w, h, kind
        self.rgba = rgba              # float32 (h, w, 4), top row first (or None)
        self.key = None
        self.sphere = False           # igTextureCubeAttr holding a sphere-map image
        self.faces = None             # six cube faces (rgba, name, key) when present


class MeshData:
    pass


class Model:
    def __init__(self):
        self.name = ""
        self.bones = []               # file bone 0 (dummy root) is excluded
        self.meshes = []
        self.warnings = []
        self.notes = []
        self.version = 0
        self.helpers = []
        self.embedded_tracks = []     # [(bone name, {times, quats, trans})]


# ------------------------------------------------------------------ skeleton
def _read_skeleton(g, model, selected=None):
    sk = [selected] if selected is not None else g.by_type("igSkeleton")
    if not sk:
        sk = g.by_type("igSkeleton2")
    if not sk:
        return
    # A mesh file may also carry a smaller attachment/default skeleton.
    actor_skeletons = [R(g,c,3) for c in g.by_type("igAnimationCombiner2")]
    sk = next((candidate for candidate in actor_skeletons if candidate in sk),sk[0])
    if sk.type == "igSkeleton2":
        infos = g.obj_list(R(g,sk,3))
        inv = g.ref(sk.get(4,-1))
        T = [b.get(5,(0,0,0)) for b in infos]
    else:
        infos = g.obj_list(R(g, sk, 4))
        trm = g.ref(sk.get(3, -1))
        inv = g.ref(sk.get(5, -1))
        if not infos or trm is None or not getattr(trm, "data", b""):
            return
        T = [struct.unpack_from("<3f", trm.data, i * 12) for i in range(len(infos))]
    n = len(infos)
    IM = ([np.array(struct.unpack_from("<16f", inv.data, i * 64)).reshape(4, 4)
           for i in range(len(inv.data) // 64)] if inv is not None and getattr(inv, "data", b"") else [])
    raw = [Bone(i, b.get(2) or "bone%d" % i, b.get(3, -1), b.get(4, -1), np.array(T[i], float))
           for i, b in enumerate(infos)]
    dummy = {b.index for b in raw if b.parent < 0 and (not (b.name or "").strip() or b.name == "Unnamed bone")}
    for b in raw:
        if b.index not in dummy and b.parent in dummy:
            b.parent = -1
    bones = [b for b in raw if b.index not in dummy]
    byidx = {b.index: b for b in bones}
    for b in bones:
        if b.parent in byidx:
            byidx[b.parent].children.append(b)
    done = set()
    # Rigid actors have no inverse-bind palette. Their combiner supplies the
    # local orientation needed to place each bone-local geometry piece.
    rigid_rot = {}
    from .pose_math import quat_to_rows
    constant_rot = {}
    conflicting = set()
    for track in g.by_type("igAnimationTrack"):
        if R(g, track, 3) is not None:
            continue
        q = np.asarray(track.get(4, (0,0,0,0)), float)
        if abs(np.linalg.norm(q)-1) > 1e-3:
            continue
        name = track.get(2)
        if name in constant_rot and abs(float(q @ constant_rot[name])) < .99999:
            conflicting.add(name)
        constant_rot[name] = q
    for b in bones:
        if b.name in constant_rot and b.name not in conflicting:
            rigid_rot[b.index] = quat_to_rows(constant_rot[b.name][None])[0].T
    if not IM:
        for comb in g.by_type("igAnimationCombiner"):
            if R(g, comb, 3) is not sk:
                continue
            for i, entries in enumerate(g.obj_list(R(g, comb, 4))):
                values = g.obj_list(entries)
                if values:
                    q = np.asarray(values[0].get(4, (0,0,0,1)), float)
                    if np.linalg.norm(q) > 1e-6:
                        rigid_rot[i] = quat_to_rows(q[None])[0].T
            break

    def solve(b):
        if b.index in done:
            return
        p = byidx.get(b.parent)
        if p is not None:
            solve(p)
        if 0 <= b.bm < len(IM):
            Wrow = np.linalg.inv(IM[b.bm])
            b.rot_world = Wrow[:3, :3].T.copy()
            b.pos_world = Wrow[3, :3].copy()
        elif p is not None:                          # helper / rigid joint without inverse bind
            b.rot_world = p.rot_world @ rigid_rot.get(b.index, np.eye(3))
            b.pos_world = p.pos_world + p.rot_world @ b.trans
        else:
            b.rot_world = rigid_rot.get(b.index, np.eye(3)).copy()
            b.pos_world = b.trans.copy()
        pr = p.rot_world if p is not None else np.eye(3)
        b.rot_local = np.linalg.inv(pr) @ b.rot_world
        done.add(b.index)

    for b in bones:
        solve(b)
    model.bones = bones


# ------------------------------------------------------------------ textures
def _decode_image(g, im, cache, swap):
    """-> (rgba or None, info). Cached per (igImage, swap). swap exchanges red and blue of every pixel."""
    key = (im.index, bool(swap))
    if key in cache:
        return cache[key]
    pf, w, h = im.get(11), im.get(2), im.get(3)
    mem = g.ref(im.get(13, -1))
    if mem is None or not getattr(mem, "data", b""):
        res = (None, "no pixel data")
    elif pf in (42, 43):
        clut = ref_of(g, im, "igClut")
        cm = g.ref(clut.first("MemoryRef", -1)) if clut else None
        if cm is None:
            res = (None, "missing PSP palette")
        else:
            row = w if pf == 42 else (w+1)//2
            pitch = (row+15)//16*16
            raw = np.frombuffer(mem.data, np.uint8)
            tiled = raw[:((h+7)//8)*pitch*8].reshape((h+7)//8,pitch//16,8,16)
            linear = tiled.transpose(0,2,1,3).reshape(-1,pitch)[:h,:row]
            idx = linear if pf == 42 else np.stack((linear & 15, linear >> 4),axis=-1).reshape(h,-1)[:,:w]
            pal = np.frombuffer(cm.data,np.uint8).reshape(-1,4).astype(np.float32)/255
            arr = pal[idx]
            res = (np.ascontiguousarray(arr[..., [2,1,0,3]]) if swap else arr, "PSP palette")
    elif pf in (65536, 65537):                              # PS2-style 8-bit palettised image + igClut
        clut = ref_of(g, im, "igClut")
        cm = g.ref(clut.first("MemoryRef", -1)) if clut is not None else None
        if cm is None or not getattr(cm, "data", b"") or len(mem.data) < (w * h if pf == 65536 else ((w+1)//2)*h):
            res = (None, "palettised image without usable palette")
        else:
            pal = np.frombuffer(cm.data, np.uint8).reshape(-1, 4).astype(np.float32) / 255.0
            if pal[:, 3].max() <= 0.5019:           # PS2 alpha range is 0..128
                pal[:, 3] = np.minimum(pal[:, 3] * 2.0, 1.0)
            if pf == 65537:
                packed = np.frombuffer(mem.data, np.uint8)[:((w+1)//2)*h].reshape(h,-1)
                idx = np.stack((packed & 15, packed >> 4), axis=-1).reshape(h,-1)[:,:w]
            else:
                idx = np.frombuffer(mem.data, np.uint8)[: w * h].reshape(h, w)
            arr = np.ascontiguousarray(pal[np.minimum(idx, len(pal) - 1)])
            res = (np.ascontiguousarray(arr[..., [2, 1, 0, 3]]) if swap else arr, "P4" if pf == 65537 else "P8")
    else:
        try:
            arr, _ = dxt.decode_pixels(pf, mem.data, w, h, im.get(19))
            if arr is None:
                res = (None, _)
            else:
                res = (np.ascontiguousarray(arr[..., [2, 1, 0, 3]]) if swap else arr, "pfmt %s" % pf)
        except Exception as e:
            res = (None, "error: %s" % e)
    cache[key] = res
    return res


def _make_texture(g, bind, st):
    """bind: igTextureBindAttr -> Texture (or None)."""
    ta = bind if bind.is_a("igTextureAttr") else ref_of(g, bind, "igTextureAttr")          # igTextureCubeAttr derives from it? check both
    sphere = False
    if ta is None:
        ta = ref_of(g, bind, "igTextureCubeAttr")
        sphere = ta is not None
    if ta is None:
        return None
    im = ref_of(g, ta, "igImage")
    if im is None:
        return None
    unit = 3 if bind.is_a("igTextureCubeAttr") else bind.first("Int", 0)
    name = (im.get(22) or "").replace("\\", "/").split("/")[-1]
    swap = st.swap and unit != 1                   # normal maps are never colour-swapped
    arr, info = _decode_image(g, im, st.cache, swap)
    t = Texture(unit, name, im.get(2), im.get(3), info, arr)
    t.key = im.index
    t.sphere = sphere or ta.type == "igTextureCubeAttr"
    t.faces = None
    if t.sphere and isinstance(ta.fields.get(19), (bytes, bytearray)) and len(ta.fields[19]) >= 24:
        ids = struct.unpack("<6i", bytes(ta.fields[19][:24]))
        faces = []
        for i in ids:
            fim = g.ref(i) if i >= 0 else None
            if fim is None or not isinstance(fim, Obj) or fim.type != "igImage":
                faces = None
                break
            fa, _ = _decode_image(g, fim, st.cache, swap)
            if fa is None:
                faces = None
                break
            faces.append((fa, (fim.get(22) or "").replace("\\", "/").split("/")[-1], fim.index))
        # Alchemy serializes NEG_X, POS_X, NEG_Y, POS_Y, NEG_Z, POS_Z.
        # Keep the Blender-facing representation positive-first.
        t.faces = [faces[i] for i in (1,0,3,2,5,4)] if faces else None
    return t


# -------------------------------------------------------- inherited render state
class State:
    def __init__(self, cache, swap):
        self.cache, self.swap = cache, swap
        self.binds, self.enabled = {}, {}
        self.material, self.color = None, None
        self.blend, self.lighting, self.cull = None, None, None
        self.alpha_test, self.alpha_func, self.alpha_ref = False, 6, 0.5
        self.depth_write = None
        self.segment = None
        self.source_name = None
        self.params = {}
        self.blend_func = None        # (src, dst) igBlendFunctionAttr enums
        self.cull_mode = 0
        self.texmat = {}              # unit -> igTextureMatrixStateAttr enabled
        self.uv_xform = None          # texture-matrix transform captured from an igTransform
        self.vbml = None              # igVertexBlendMatrixListAttr: igMatrixObject indices

    def copy(self):
        s = State(self.cache, self.swap)
        s.binds, s.enabled = dict(self.binds), dict(self.enabled)
        s.material, s.color = self.material, self.color
        s.blend, s.lighting, s.cull = self.blend, self.lighting, self.cull
        s.alpha_test, s.alpha_func, s.alpha_ref = self.alpha_test, self.alpha_func, self.alpha_ref
        s.depth_write = self.depth_write
        s.segment = self.segment
        s.source_name = self.source_name
        s.params = dict(self.params)
        s.blend_func, s.texmat, s.uv_xform, s.vbml = self.blend_func, dict(self.texmat), self.uv_xform, self.vbml
        s.cull_mode = self.cull_mode
        return s

    def apply(self, g, attrs):
        for a in attrs:
            if not isinstance(a, Obj):
                continue
            t = a.type
            if t in ("igTextureBindAttr", "igTextureCubeAttr", "igTextureAttr"):
                tex = _make_texture(g, a, self)
                if tex is not None:
                    self.binds[tex.unit] = tex
            elif t == "igTextureStateAttr":
                self.enabled[a.first("Int", 0)] = bool(a.first("Bool", 1))
            elif t == "igMaterialAttr":
                v4 = [v for _, v in a.all_of("Vec4f")]
                if len(v4) >= 4:
                    self.material = {"shininess": a.first("Float", 0.0), "diffuse": v4[0], "ambient": v4[1],
                                     "emission": v4[2], "specular": v4[3]}
            elif t == "igColorAttr":
                v = a.first("Vec4f")
                if v is not None:
                    self.color = v
            elif t == "igDepthWriteStateAttr":
                self.depth_write = bool(a.first("Bool",1))
            elif t == "igAlphaStateAttr":
                self.alpha_test = bool(a.first("Bool", 0))
            elif t == "igAlphaFunctionAttr":
                self.alpha_func, self.alpha_ref = a.first("Enum", 4), a.first("Float", 0.5)
            elif t == "igBlendStateAttr":
                self.blend = bool(a.first("Bool", 0))
            elif t == "igBlendFunctionAttr":
                en = [v for _, v in a.all_of("Enum")]
                if len(en) >= 2:
                    self.blend_func = (en[0], en[1])
            elif t == "igTextureMatrixStateAttr":
                self.texmat[a.first("Int", 0)] = bool(a.first("Bool", 1))
            elif t == "igVertexBlendMatrixListAttr":
                ml = ref_of(g, a, "igObjectList")
                self.vbml = [c.index for c in g.obj_list(ml) if c is not None] if ml is not None else None
            elif t == "igLightingStateAttr":
                self.lighting = bool(a.first("Bool", 1))
            elif t == "igCullFaceAttr":
                self.cull = bool(a.first("Bool", 0))
                self.cull_mode = a.first("Enum", 0)
            elif t == "igShaderParametersAttr":
                dl = ref_of(g, a, "igObjectList")
                for c in g.obj_list(dl):
                    if c is not None and c.type.startswith("igShaderConstant"):
                        val = c.first("Vec4f", None)
                        if c.type == "igShaderConstantBool":
                            val = bool(c.get(5,0))
                        elif val is None:
                            val = c.first("Bool", None)
                        self.params[c.get(2)] = val

    def apply_shader_data(self, g, node):
        """igInterpretedShader (raw 3ds Max export): name -> value map of the Ether shader settings."""
        data = ref_of(g, node, "igInterpretedShaderData")
        sm = ref_of(g, data, "igObjectStringMap") if data is not None else None
        if sm is None:
            return
        keys = g.obj_list(g.ref(sm.fields.get(2, -1)))
        vals = g.obj_list(g.ref(sm.fields.get(3, -1)))

        def val(o):
            for k in ("Vec4f", "Float", "Bool", "Int"):
                r = o.first(k)
                if r is not None:
                    return r
            return None

        d = {k.get(2): val(v) for k, v in zip(keys, vals) if k is not None and v is not None}
        mat = dict(self.material) if self.material else {"shininess": 0.0, "diffuse": (1, 1, 1, 1), "ambient": (1, 1, 1, 1),
                                                         "emission": (0, 0, 0, 0), "specular": (0, 0, 0, 0)}
        for key, name in (("DiffuseColor", "diffuse"), ("AmbientColor", "ambient"), ("SpecularColor", "specular"),
                          ("EmissiveColor", "emission")):
            if d.get(key) is not None:
                mat[name] = tuple(d[key])
        if d.get("SpecularExponent") is not None:
            mat["shininess"] = float(d["SpecularExponent"])
        self.material = mat
        if "BlendType" in d and d["BlendType"] is not None:
            bt = int(d["BlendType"])
            self.blend = bt != 0
            self.blend_func = {1: (4, 5), 2: (4, 1), 3: (1, 5)}.get(bt) if bt else None
        if d.get("Lighting") is not None:
            self.lighting = bool(d["Lighting"])
        if d.get("Reflectance") is not None:
            self.params["material.reflectance"] = tuple(d["Reflectance"])
        if d.get("Transparent") is not None:
            self.params["material.transparent"] = bool(d["Transparent"])

    def textures(self):
        textures = {u: t for u, t in self.binds.items() if self.enabled.get(u, True)}
        if self.params.get("legacy_shader") == "YuGiOh-Shader":
            # Xbox shader stages: colour, scalar mask, generated reflection.
            # Expose these roles instead of mistaking stage 1 for a normal map.
            import copy
            result = {}
            for old, new in ((0,0),(1,4),(2,3)):
                if old not in textures:
                    continue
                tex = copy.copy(textures[old]);tex.unit = new
                if new == 3:tex.sphere = True
                if old == 1 and self.swap and tex.rgba is not None:
                    tex.rgba = np.ascontiguousarray(tex.rgba[..., [2,1,0,3]])
                result[new] = tex
            return result
        return textures


# ------------------------------------------------------------------ geometry
def _strip_to_tris(idx):
    out = []
    for i in range(len(idx) - 2):
        a, b, c = idx[i], idx[i + 1], idx[i + 2]
        if a == b or b == c or a == c:
            continue
        out.append((a, b, c) if i % 2 == 0 else (a, c, b))
    return out


def _ext_slots(g, mem):
    """igExternalIndexedEntry memory block -> {slot: Mem}."""
    if mem is None or mem.size not in (76, 80) or len(mem.data) < mem.size:
        return {}
    vals = struct.unpack("<%dI" % (mem.size // 4), mem.data[:mem.size])
    out = {}
    for i, v in enumerate(vals):
        if v != 0xFFFFFFFF and v < len(g.objects) and isinstance(g.objects[v], Mem):
            out[i] = g.objects[v]
    return out


def _faces(g, ia, pl, prim, nv, warns, name):
    """Triangles from an index array / primitive-length array (lists and strips)."""
    imem = g.ref(ia.first("MemoryRef", -1)) if ia is not None else None
    if imem is not None and getattr(imem, "data", b""):
        cnt = min(ia.first("UnsignedInt", len(imem.data) // 2), len(imem.data) // 2)
        idx = np.frombuffer(imem.data, "<u2")[:cnt].astype(np.int64)
        indexed = True
    else:
        idx = np.arange(nv, dtype=np.int64)
        indexed = False
    if prim == PRIM_TRI_LIST:
        idx = idx[: len(idx) // 3 * 3]
        return idx.reshape(-1, 3), indexed
    if prim == PRIM_TRI_STRIP:
        lens = []
        pmem = g.ref(pl.first("MemoryRef", -1)) if pl is not None else None
        if pmem is not None and getattr(pmem, "data", b""):
            n = pl.first("UnsignedInt", 0)
            bits = [v for _, v in pl.all_of("UnsignedInt")]
            bits = bits[1] if len(bits) > 1 else 32
            dt = "<u2" if bits == 16 else "<u4"
            lens = np.frombuffer(pmem.data, dt)[:n].astype(int).tolist()
        if not lens:
            lens = [len(idx)]
        tris, pos = [], 0
        for L in lens:
            tris += _strip_to_tris(idx[pos:pos + L].tolist())
            pos += L
        return np.array(tris, np.int64).reshape(-1, 3), indexed
    warns.append("%s: primitive type %s not supported" % (name, prim))
    return None, False


def _geom_v1(g, ga, warns, name):
    va = ref_of(g, ga, "igVertexArray")
    if va is None:
        return None
    ia, pl = ref_of(g, ga, "igIndexArray"), ref_of(g, ga, "igPrimLengthArray")
    prim = ga.first("Enum", PRIM_TRI_LIST)
    nv = va.first("UnsignedInt", 0)
    fmt_raw = va.first("Struct")
    fmt = struct.unpack("<I", fmt_raw[:4])[0] if isinstance(fmt_raw, (bytes, bytearray)) and len(fmt_raw) >= 4 else 0
    ext = _ext_slots(g, g.ref(va.get(2, -1)))
    if EXT_POS not in ext or nv == 0:
        warns.append("%s: vertex stream missing" % name)
        return None
    m = MeshData()
    m.positions = np.frombuffer(ext[EXT_POS].data, "<f4")[:nv * 3].reshape(-1, 3).astype(np.float32)
    nv = len(m.positions)
    m.normals = (np.frombuffer(ext[EXT_NORMAL].data, "<f4")[:nv * 3].reshape(-1, 3).astype(np.float32)
                 if EXT_NORMAL in ext and len(ext[EXT_NORMAL].data) >= nv * 12 else None)
    m.uv = (np.frombuffer(ext[EXT_UV].data, "<f4")[:nv * 2].reshape(-1, 2).astype(np.float32)
            if EXT_UV in ext and len(ext[EXT_UV].data) >= nv * 8 else None)
    m.joints = m.weights = None
    nbw, nbi = (fmt & VF_BW_MASK) >> VF_BW_SHIFT, (fmt & VF_BI_MASK) >> VF_BI_SHIFT
    wm, im_ = g.ref(va.get(7, -1)), g.ref(va.get(8, -1))
    if nbw and nbi and isinstance(wm, Mem) and isinstance(im_, Mem) and len(wm.data) >= nv * nbw * 4 and len(im_.data) >= nv * nbi:
        k = min(nbw, nbi, 4)
        m.weights = np.zeros((nv, 4), np.float32)
        m.joints = np.zeros((nv, 4), np.int32)
        m.weights[:, :k] = np.frombuffer(wm.data, "<f4")[:nv * nbw].reshape(nv, nbw)[:, :k]
        m.joints[:, :k] = np.frombuffer(im_.data, np.uint8)[:nv * nbi].reshape(nv, nbi)[:, :k]
    faces, indexed = _faces(g, ia, pl, prim, nv, warns, name)
    if faces is None:
        return None
    faces = faces[(faces < nv).all(axis=1)] if len(faces) else faces
    m.faces = faces
    m.indexed = indexed
    m.has_colors = EXT_COLOR in ext
    m.colors = None
    if m.has_colors:
        raw = ext[EXT_COLOR].data
        if len(raw) >= nv*16:
            m.colors = np.frombuffer(raw, '<f4')[:nv*4].reshape(nv,4).copy()
        elif len(raw) >= nv*4:
            m.colors = np.frombuffer(raw, 'u1')[:nv*4].reshape(nv,4).astype(np.float32)/255

    return m


def _geom_v2(g, ga, warns, name):
    va = ref_of(g, ga, "igVertexArray2")
    if va is None:
        return None
    vd = {}
    for d in g.obj_list(ref_of(g, va, "igObjectList")):
        lst = ref_of(g, d, "igDataList")
        if lst is not None:
            vd.setdefault(d.get(4), (d, lst))
    stream = ref_of(g, va, "igVertexStream")
    if stream is not None:
        # Older PSP files serialize the component-list index in slot 5.
        components = g.ref(stream.get(5, -1))
        if components is not None and components.is_a("igObjectList"):
            for d in g.obj_list(components):
                lst = ref_of(g, d, "igDataList")
                if lst is not None:
                    vd[d.get(4)] = (d, lst)
    def component(usage, width):
        d, lst = vd[usage]
        types = {"igShortList": "<i2", "igUnsignedShortList": "<u2", "igCharList": "i1", "igUnsignedCharList": "u1", "igUnsignedIntList": "<u4"}
        mem = g.ref(lst.first("MemoryRef", -1))
        dtype = types.get(lst.type, "<f4")
        a = np.frombuffer(mem.data, dtype).astype(np.float32)
        ncomp = int(d.get(6, 1)) if lst.type in types else width
        a = a[:len(a)//ncomp*ncomp].reshape(-1, ncomp)
        if usage != U_BONEIDX:
            a = a / (2.0 ** d.get(8, 0))
            a = a * np.asarray(d.get(9, (1,1,1,1)))[:ncomp] + np.asarray(d.get(10, (0,0,0,0)))[:ncomp]
        return a
    if U_POS not in vd:
        warns.append("%s: geometry without positions skipped" % name)
        return None
    prim, count = ga.get(7), ga.get(8, 0)
    m = MeshData()
    m.positions = component(U_POS, 3)
    nv = len(m.positions)
    m.normals = (component(U_NORMAL, 3) if U_NORMAL in vd else None)
    m.uv = (component(U_UV, 2) if U_UV in vd else None)
    for nm in ("normals", "uv"):
        a = getattr(m, nm)
        if a is not None and len(a) != nv:
            warns.append("%s: %s stream length mismatch, ignored" % (name, nm))
            setattr(m, nm, None)
    m.joints = m.weights = None
    if U_WEIGHT in vd and U_BONEIDX in vd:
        wf = component(U_WEIGHT, 1).reshape(-1)
        jf = component(U_BONEIDX, 1).astype(np.int32).reshape(-1)
        sw, sj = max(1, len(wf) // max(nv, 1)), max(1, len(jf) // max(nv, 1))
        k = min(sw, sj, 4)
        m.weights = np.zeros((nv, 4), np.float32)
        m.joints = np.zeros((nv, 4), np.int32)
        m.weights[:, :k] = wf[: nv * sw].reshape(nv, sw)[:, :k]
        m.joints[:, :k] = jf[: nv * sj].reshape(nv, sj)[:, :k]
    m.has_colors = 2 in vd
    m.colors = None
    if m.has_colors:
        d, lst = vd[2]
        mem = g.ref(lst.first("MemoryRef", -1))
        if lst.type == 'igVec4fList':
            m.colors = np.frombuffer(mem.data, '<f4')[:nv*4].reshape(nv,4).copy()
        elif len(mem.data) >= nv*4:
            m.colors = np.frombuffer(mem.data, 'u1')[:nv*4].reshape(nv,4).astype(np.float32)/255

    ia, pl = ref_of(g, ga, "igIndexArray"), ref_of(g, ga, "igPrimLengthArray")
    if prim == PRIM_TRI_LIST and (ia is None or not getattr(g.ref(ia.first("MemoryRef", -1)), "data", b"")):
        if nv < count * 3:
            warns.append("%s: %d vertices for %d triangles, truncated" % (name, nv, count))
            count = nv // 3
        m.faces = np.arange(count * 3, dtype=np.int64).reshape(-1, 3)
        m.indexed = False
    else:
        faces, indexed = _faces(g, ia, pl, prim, nv, warns, name)
        if faces is None:
            return None
        m.faces, m.indexed = faces[(faces < nv).all(axis=1)] if len(faces) else faces, indexed
    return m


def _dedupe(mesh):
    """Weld identical vertices (exporters often write un-indexed triangle soup)."""
    cols = [mesh.positions]
    if getattr(mesh, "colors", None) is not None:
        cols.append(mesh.colors)
    for a in (mesh.normals, mesh.uv):
        if a is not None:
            cols.append(a)
    if mesh.joints is not None:
        cols += [mesh.joints.astype(np.float32), mesh.weights]
    key = np.ascontiguousarray(np.concatenate(cols, axis=1).astype(np.float32))
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    mesh.positions = mesh.positions[first]
    if getattr(mesh, "colors", None) is not None:
        mesh.colors = mesh.colors[first]
    if mesh.normals is not None:
        mesh.normals = mesh.normals[first]
    if mesh.uv is not None:
        mesh.uv = mesh.uv[first]
    if mesh.joints is not None:
        mesh.joints, mesh.weights = mesh.joints[first], mesh.weights[first]
    mesh.faces = inv[mesh.faces]


def _finish_mesh(m, name, st, bmlist, rigid_bone, world, bm_to_bone, bone_by_name, warns, mo_to_bone=None):
    # Weighted vertices are already in skeleton bind space. The palette supplies
    # their model-view matrices; enclosing rigid transforms must not be baked in.
    if m.joints is not None and bm_to_bone and bmlist:
        world = None
    # bone indices: vertex idx -> this piece's blend-matrix list -> bm index -> bone
    if m.joints is not None and st.vbml is not None and mo_to_bone:
        direct = np.array([mo_to_bone.get(i, -1) for i in st.vbml] or [-1], np.int32)
        m.joints = direct[np.clip(m.joints, 0, len(direct) - 1)]
    elif m.joints is not None:
        lut_src = np.array(bmlist if bmlist else range(256), np.int64)
        bmi = lut_src[np.clip(m.joints, 0, len(lut_src) - 1)]
        top = max(list(bm_to_bone) + [0]) + 1
        lut = np.full(max(top, int(bmi.max()) + 1), -1, np.int32)
        for bm, bi in bm_to_bone.items():
            lut[bm] = bi
        m.joints = lut[bmi]
    elif rigid_bone is not None:                       # rigid part attached to one bone
        n = len(m.positions)
        m.joints = np.zeros((n, 4), np.int32)
        m.joints[:, 0] = rigid_bone
        m.weights = np.zeros((n, 4), np.float32)
        m.weights[:, 0] = 1.0
    if world is not None and not np.allclose(world, np.eye(4)):
        M3, t = world[:3, :3], world[3, :3]
        m.positions = (m.positions @ M3 + t).astype(np.float32)
        if m.normals is not None:
            nn = m.normals @ M3
            m.normals = (nn / np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-9)).astype(np.float32)
    if not m.indexed:
        _dedupe(m)
    m.name = name
    m.flags = getattr(m, "flags", 0)
    m.alpha_test, m.alpha_func, m.alpha_ref = st.alpha_test, st.alpha_func, st.alpha_ref
    m.depth_write = st.depth_write
    m.segment = st.segment
    m.textures = st.textures()
    m.material = dict(st.material) if st.material else {}
    m.color = st.color
    m.blend, m.lighting, m.cull = st.blend, st.lighting, st.cull
    m.blend_func = st.blend_func
    m.cull_mode = st.cull_mode
    m.texture_matrix_states = dict(st.texmat)
    m.uv_anim = st.uv_xform
    m.params = dict(st.params)
    sig = (tuple(sorted((u, t.key) for u, t in m.textures.items())),
           repr(sorted((k, tuple(v)) for k, v in m.material.items() if isinstance(v, (tuple, list))))
           + repr(m.material.get("shininess")),
           tuple(m.color) if m.color is not None else None, m.blend, m.lighting, m.cull,
           repr(sorted((k, str(v)) for k, v in m.params.items())), m.blend_func,
           id(st.uv_xform["seq"]) if st.uv_xform and st.uv_xform.get("seq") is not None else None)
    m.material_key = (sig, st.alpha_test, st.alpha_func, st.alpha_ref, tuple(sorted(st.texmat.items())), st.depth_write)
    return m


# ------------------------------------------------------------------ scene walk
def _seq_data(g, seq):
    """igTransformSequence1_5 -> dict(times seconds, quats xyzw, trans or None) or None."""
    if seq is None:
        return None
    tl = ref_of(g, seq, "igLongList")
    ql = ref_of(g, seq, "igQuaternionfList")
    vls = [t for t in refs(g, seq) if isinstance(t, Obj) and t.type == "igVec3fList"]
    if tl is None:
        return None
    times = np.array(g.data_list(tl, "q"), np.float64) / 1e9
    if len(times) == 0:
        return None
    n = len(times)
    q = (np.array(g.data_list(ql, "4f"), np.float64).reshape(-1, 4) if ql is not None
         else np.tile([0, 0, 0, 1.0], (n, 1)))
    tr = np.array(g.data_list(vls[0], "3f"), np.float64).reshape(-1, 3) if vls else None
    sc = np.array(g.data_list(vls[1], "3f"), np.float64).reshape(-1, 3) if len(vls) > 1 else None
    n = min(n, len(q), len(tr) if tr is not None else n)
    return {"times": times[:n], "quats": q[:n], "trans": tr[:n] if tr is not None else None,
            "scale": sc[:n] if sc is not None and len(sc) >= n else None}


class _Ctx:
    """Walk context: node-based bones (igJoint / animated igTransform) and their animation."""
    def __init__(self, g, model, by_name, bm_to_bone):
        self.g, self.model, self.by_name, self.bm_to_bone = g, model, by_name, bm_to_bone
        self.node_bone = {}           # node index -> Bone
        self.mo_to_bone = {}          # igMatrixObject index -> bone index
        self.anims = []               # (bone name, seq data)
        self.next_index = (max([b.index for b in model.bones]) + 1) if model.bones else 1
        self.used = {b.name for b in model.bones}

    def new_bone(self, node, parent_bone, world_row, base):
        name = (node.get(2) or "").strip() or "%s_%d" % (base, len(self.node_bone))
        n0, k = name, 1
        while name in self.used:
            k += 1
            name = "%s_%d" % (n0, k)
        self.used.add(name)
        b = Bone(self.next_index, name, parent_bone.index if parent_bone is not None else -1, -1, np.zeros(3))
        self.next_index += 1
        b.rot_world = np.array(world_row)[:3, :3].T.copy()
        b.pos_world = np.array(world_row)[3, :3].copy()
        b.added = False
        if parent_bone is not None:
            parent_bone.children.append(b)
            b.rot_local = parent_bone.rot_world.T @ b.rot_world
        else:
            b.rot_local = b.rot_world.copy()
        self.model.bones.append(b)
        self.node_bone[node.index] = b
        return b


def _prepass_joints(g, cx):
    """igJoint nodes form a scene-graph skeleton; build bones (bind world from the inverse bind matrix)."""
    joints = g.by_type("igJoint")
    if not joints:
        return
    parent = {}
    for o in g.objects:
        if isinstance(o, Obj) and o.is_a("igNodeList"):
            for c in g.obj_list(o):
                if c is not None:
                    parent[c.index] = o
    owner = {}
    for o in g.objects:
        if isinstance(o, Obj) and o.is_a("igNode"):
            for l in refs(g, o):
                if isinstance(l, Obj) and l.is_a("igNodeList"):
                    owner[l.index] = o
    def pnode(n):
        lst = parent.get(n.index)
        return owner.get(lst.index) if lst is not None else None
    ordered, seen = [], set()
    def visit(j):
        if j.index in seen:
            return
        seen.add(j.index)
        p = pnode(j)
        while p is not None and not p.is_a("igJoint"):
            p = pnode(p)
        if p is not None:
            visit(p)
        ordered.append((j, p))
    for j in joints:
        visit(j)
    for j, p in ordered:
        mats = [np.array(v, np.float64).reshape(4, 4) for _, v in j.all_of("Matrix44f")]
        local = mats[0] if mats else np.eye(4)
        inv = mats[1] if len(mats) > 1 else None
        pb = cx.node_bone.get(p.index) if p is not None else None
        if inv is not None and abs(np.linalg.det(inv[:3, :3])) > 1e-6:
            world = np.linalg.inv(inv)
        else:
            world = local @ (np.array(pb.rot_world.T.tolist() + [[0, 0, 0]]) if False else np.eye(4)) if pb is None else None
            if pb is None:
                world = local
            else:
                pw = np.eye(4)
                pw[:3, :3], pw[3, :3] = pb.rot_world.T, pb.pos_world
                world = local @ pw
        b = cx.new_bone(j, pb, world, "joint")
        mo = ref_of(g, j, "igMatrixObject")
        if mo is not None:
            cx.mo_to_bone[mo.index] = b.index
        sd = _seq_data(g, ref_of(g, j, "igTransformSequence1_5"))
        if sd is not None:
            cx.anims.append((b.name, sd))


def _walk(cx, n, st, bmlist, rigid, world, out, depth=0, helper_parent=None):
    g = cx.g
    if n is None or depth > 200:
        return
    st = st.copy()
    for lst in refs(g, n):
        if isinstance(lst, Obj) and lst.is_a("igAttrList"):
            st.apply(g, g.obj_list(lst))
    if n.is_a("igSegment"):
        st.segment = n.get(2)
    if n.type in ("igEnvironmentMapShader2", "igDx8EnvironmentMapShader"):
        cube = ref_of(g, n, "igTextureCubeAttr")
        if cube is not None:
            tex = _make_texture(g, cube, st)
            if tex is not None:
                st.binds[3] = tex
                st.enabled[3] = True
        bind = ref_of(g, n, "igTextureBindAttr")
        if bind is not None:
            st.apply(g, [bind])
        st.params["material.reflectance"] = (n.get(11, 1.0), 0, 0, 0)
    if n.type == "igSimpleShader":
        st.params["legacy_shader"] = n.get(2)
        data = ref_of(g, n, "igShaderData")
        if data is not None:
            passes = g.obj_list(R(g, data, 2))
            if passes:
                st.apply(g, g.obj_list(passes[0]))
            if n.get(2) == "EnvmapShader" and 0 in st.binds and 1 in st.binds:
                # This fixed-function shader uses reflection in unit 0 and
                # diffuse colour in unit 1, unlike MUA Ether's normal slot.
                import copy
                env, diffuse = copy.copy(st.binds[0]), copy.copy(st.binds[1])
                env.unit, env.sphere, diffuse.unit = 3, True, 0
                if st.swap and diffuse.rgba is not None:
                    diffuse.rgba = np.ascontiguousarray(diffuse.rgba[..., [2,1,0,3]])
                st.binds = {0: diffuse, 3: env}
                st.enabled = {0: True, 3: True}
    if n.is_a("igInterpretedShader"):
        st.apply_shader_data(g, n)
    if n.is_a("igBlendMatrixSelect"):
        if n.get(2):
            st.source_name = n.get(2).removesuffix(" Skin")
        il = ref_of(g, n, "igIntList")
        if il is not None:
            bmlist = g.data_list(il, "i")
        rigid = None
    if n.is_a("igModelViewMatrixBoneSelect"):
        b = cx.by_name.get(n.get(2))
        rigid = b.index if b is not None else rigid
        if b is not None and not cx.bm_to_bone:
            world = np.eye(4)
            world[:3,:3], world[3,:3] = b.rot_world.T, b.pos_world
    if n.is_a("igJoint") and n.index in cx.node_bone:
        rigid = cx.node_bone[n.index].index
    if n.is_a("igTransform") and not n.is_a("igJoint"):
        M = n.first("Matrix44f")
        seq = _seq_data(g, ref_of(g, n, "igTransformSequence1_5"))
        target = n.get(9, 0)
        if 9 <= target <= 16:                    # TEXTURE_MATRIX_0 .. TEXTURE_MATRIX_7
            # TextureMatrixState enables a texture unit; it does not change the
            # target of nested model-view transforms (e.g. handfountain).
            u = target - 9
            st.uv_xform = {"unit": u, "matrix": np.array(M, np.float64).reshape(4, 4) if M is not None else np.eye(4),
                           "seq": ref_of(g, n, "igTransformSequence1_5"), "data": seq}
        else:
            if M is not None:
                world = np.array(M, np.float64).reshape(4, 4) @ (world if world is not None else np.eye(4))
            if n.get(2) and seq is None and n.get(2) not in cx.by_name:
                cx.model.helpers.append(dict(index=n.index, name=n.get(2), parent=helper_parent,
                                             matrix=np.array(world if world is not None else np.eye(4)), flags=n.get(5,0)))
                helper_parent = n.index
            if seq is not None and len(seq["times"]) > 1:        # animated node -> rigid bone
                parent_bone = next((b for b in cx.model.bones if b.index == rigid), None)
                b = cx.new_bone(n, parent_bone, world if world is not None else np.eye(4), "node")
                cx.anims.append((b.name, seq))
                rigid = b.index
    if n.is_a("igGeometry"):
        out.append((n, st, bmlist, rigid, world))
        return
    for lst in refs(g, n):
        if isinstance(lst, Obj) and lst.is_a("igNodeList"):
            for c in g.obj_list(lst):
                _walk(cx, c, st, bmlist, rigid, world, out, depth + 1, helper_parent)


def _roots(g):
    if g.version >= 10:
        # Asset containers own node lists too; these do not parent their nodes
        # in the scene graph (Actor2's appearance is stored in asShapeInfo).
        child = {c.index for node in g.by_type("igNode") for lst in refs(g,node)
                 if isinstance(lst,Obj) and lst.is_a("igNodeList")
                 for c in g.obj_list(lst) if c is not None}
        return [o for o in g.by_type("igNode") if o.index not in child]
    child = set()
    for o in g.objects:
        if isinstance(o, Obj) and o.is_a("igNodeList"):
            for c in g.obj_list(o):
                if c is not None:
                    child.add(c.index)
    return [o for o in g.objects if isinstance(o, Obj) and o.is_a("igNode") and o.index not in child]


def load_model(path, dxt_mode="AUTO", _g=None, _skin=None, _skeleton=None):
    """dxt_mode: NONE (colours as stored) or SWAP (red/blue exchanged on every texture except normal maps)."""
    g = _g if _g is not None else IGB(path)
    m = Model()
    m.version = g.version
    swap = dxt_mode == "SWAP"
    m.dxt_swapped = swap
    skins = [_skin] if _skin is not None else g.by_type("igSkin")
    actors = g.by_type("igActor")
    stem = path.replace("\\", "/").split("/")[-1].rsplit(".", 1)[0]
    m.name = (skins[0].get(2) if skins else None) or (actors[0].get(2) if actors else None) or stem
    _read_skeleton(g, m, _skeleton)
    m.source_skeleton = _skeleton.get(2) if _skeleton is not None else None
    bm_to_bone = {b.bm: b.index for b in m.bones if b.bm >= 0}
    by_name = {b.name: b for b in m.bones}
    st0 = State({}, swap)
    cx = _Ctx(g, m, by_name, bm_to_bone)
    _prepass_joints(g, cx)
    found = []
    skin_roots = {}
    if g.version <= 6 and len(skins) > 1:
        for source_skin in skins:
            node = R(g, source_skin, 3)
            if node is not None:
                skin_roots[node.index] = source_skin.get(2) or "Skin"
    for root in ([R(g,_skin,3)] if _skin is not None else _roots(g)):
        state = st0.copy()
        if root.index in skin_roots:
            state.params["source_skin"] = skin_roots[root.index]
        _walk(cx, root, state, [], None, None, found)
    by_name = {b.name: b for b in m.bones}
    for geo, st, bm, rigid, world in found:
        gname = geo.get(2) or st.source_name or "mesh"
        for ga in g.obj_list(ref_of(g, geo, "igAttrList")):
            if ga is None or not ga.type.startswith("igGeometryAttr"):
                continue
            mesh = _geom_v2(g, ga, m.warnings, gname) if ga.type == "igGeometryAttr2" else _geom_v1(g, ga, m.warnings, gname)
            if mesh is not None and len(mesh.faces):
                mesh.flags = geo.get(5, 0) or 0
                m.meshes.append(_finish_mesh(mesh, gname, st, bm, rigid, world, bm_to_bone, by_name, m.warnings,
                                             cx.mo_to_bone))
    if cx.anims:
        stationary = [mesh for mesh in m.meshes if mesh.joints is None]
        if stationary:
            # Static siblings must not inherit the first animated node during
            # export just because the file also contains an armature.
            index = max((b.index for b in m.bones),default=-1)+1
            name = "StaticScene"
            while name in {b.name for b in m.bones}:
                name += "_"
            bone = Bone(index,name,-1,-1,np.zeros(3))
            bone.rot_world = bone.rot_local = np.eye(3)
            bone.pos_world = np.zeros(3)
            m.bones.append(bone)
            for mesh in stationary:
                mesh.joints = np.zeros((len(mesh.positions),4),np.int32)
                mesh.weights = np.zeros((len(mesh.positions),4),np.float32)
                mesh.joints[:,0],mesh.weights[:,0] = index,1.0
    m.embedded_tracks = cx.anims
    if cx.anims:
        m.notes.append("file contains %d animated node(s)/joint(s)" % len(cx.anims))
    if not m.meshes:
        m.warnings.append("no geometry found in file")
    return m


def load_models(path, dxt_mode="AUTO"):
    """Split independent, explicitly named skin/skeleton pairs in databases.

    Matching is deliberately exact: no heuristic assignment by bone count or
    first-skeleton order, which can silently deform unrelated characters.
    """
    g = IGB(path)
    if g.version >= 10 and g.by_type("asTileGeometry"):
        from .tile_geometry import load_models as load_tiles
        assets = load_tiles(g,dxt_mode=="SWAP")
        if assets:
            assets[0].notes.append("tile geometry library: assets imported separately; procedural city placement is not reconstructed")
            return assets
    skins = g.by_type("igSkin")
    named = {s.get(2):s for s in g.by_type("igSkeleton") if s.get(2)!="defaultSkeleton"}
    pairs = []
    if len(skins)>1 and len(named)>1:
        for skin in skins:
            name = skin.get(2) or ""
            stem = name.removeprefix("skin_")
            skeleton = named.get(stem+"_skeleton")
            if skeleton is None:
                # Some databases reorder name components (gm_kage / kage_gm,
                # zakuold / oldzaku). Only accept their ordered database pair
                # when the same name characters agree; never guess by size.
                for db in g.by_type("igAnimationDatabase"):
                    ds, dk = g.obj_list(R(g,db,7)), g.obj_list(R(g,db,5))
                    if len(ds)!=len(dk) or skin not in ds:
                        continue
                    candidate = dk[ds.index(skin)]
                    cname = (candidate.get(2) or "").removesuffix("_skeleton")
                    if sorted(stem.replace("_","")) == sorted(cname.replace("_","")):
                        skeleton = candidate
                        break
            if skeleton is None:
                pairs = []
                break
            pairs.append((skin,skeleton))
    if not pairs:
        return [load_model(path,dxt_mode,_g=g)]
    return [load_model(path,dxt_mode,_g=g,_skin=skin,_skeleton=sk) for skin,sk in pairs]
