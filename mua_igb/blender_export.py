"""Collect Blender scene data and write MUA1 PC .igb files (skin, static model, animation)."""
import json
import os
import numpy as np
import bpy

from . import pose_math as P
from . import blender_io as bio
from .export_core import (XBone, XTex, XMat, XMesh, XSkin, XStatic, XAnim, build_skin_file, build_static_file,
                          build_anim_file, rot_to_quat, _weld)

from .playback import PLAY_MODE as ANIM_TYPE_ENUM


# ----------------------------------------------------------------------- bones
class BoneInfo:
    """Everything the exporter needs to know about one armature bone."""
    def __init__(self, name):
        self.name = name
        self.parent = -1
        self.J = np.eye(3)            # joint frame of the armature bone (column convention)
        self.head = np.zeros(3)       # Blender units, world
        self.D = np.eye(3)            # convention offset to the game's standard frames
        self.frame = np.eye(3)        # frame written to the file (J D^T when normalising)


def find_armature(context, mesh_objs=()):
    ob = context.active_object
    if ob is not None and ob.type == "ARMATURE":
        return ob
    for o in context.selected_objects:
        if o.type == "ARMATURE":
            return o
    for m in mesh_objs:
        for mod in m.modifiers:
            if mod.type == "ARMATURE" and mod.object is not None:
                return mod.object
        if m.parent is not None and m.parent.type == "ARMATURE":
            return m.parent
    return None


def collect_bones(arm_obj, scale, normalize):
    """-> (list of BoneInfo parent-first, {name: index})"""
    arm = arm_obj.data
    Mw = np.array(arm_obj.matrix_world)
    object_scale = np.linalg.norm(Mw[:3, :3], axis=0)
    if not np.allclose(object_scale, object_scale[0], atol=1e-6, rtol=1e-6):
        raise ValueError("Apply non-uniform armature object scale before exporting; otherwise rest axes and animated translations can shear.")
    by_name, order = {}, []

    def visit(b):
        if b.name in by_name:
            return
        if b.parent is not None:
            visit(b.parent)
        info = BoneInfo(b.name)
        M = Mw @ np.array(b.matrix_local)
        B3 = M[:3, :3] / np.maximum(np.linalg.norm(M[:3, :3], axis=0), 1e-12)
        if np.linalg.det(B3) <= 0 or not np.allclose(B3.T @ B3, np.eye(3), atol=2e-5):
            raise ValueError("Armature transform creates mirrored or sheared rest axes. Apply the armature object scale before exporting.")
        info.J = P.joint_frame_from_blender(B3)
        info.head = M[:3, 3].copy()
        info.parent = by_name[b.parent.name] if b.parent is not None else -1
        by_name[b.name] = len(order)
        order.append(info)

    for b in arm.bones:
        visit(b)
    for info in order:
        info.D = np.eye(3)
        info.frame = info.J @ info.D.T
    return order, by_name


def xbones(infos, scale):
    return [XBone(i.name, i.parent, i.head / scale, i.frame) for i in infos]


# --------------------------------------------------------------------- materials
def _is_ph(img):
    return img is None or img.name.startswith("MUA_placeholder")


def _resize(arr, n):
    h, w = arr.shape[:2]
    if (h, w) == (n, n):
        return arr
    ys = np.clip(((np.arange(n) + 0.5) * h / n).astype(int), 0, h - 1)
    xs = np.clip(((np.arange(n) + 0.5) * w / n).astype(int), 0, w - 1)
    return arr[ys][:, xs]


def normal_to_dxt5nm(arr, flip_green):
    """OpenGL tangent-space normal map RGB -> MUA layout: X in alpha, Y in green, red and blue black."""
    x = arr[..., 0] * 2 - 1
    y = arr[..., 1] * 2 - 1
    if flip_green:
        y = -y
    out = np.zeros_like(arr)
    out[..., 1] = y * 0.5 + 0.5
    out[..., 3] = x * 0.5 + 0.5
    return out


class Collector:
    def __init__(self, o, scale):
        self.o, self.scale = o, scale
        self.tex_cache, self.mat_cache = {}, {}
        self.warnings = []

    def tex(self, img, kind, role="color"):
        if _is_ph(img):
            return None
        key = (id(img), kind, role)
        if key not in self.tex_cache:
            arr = bio.image_array(img)
            if role == "normal":
                arr = normal_to_dxt5nm(arr, self.o.flip_green)
            name = os.path.splitext(os.path.basename(img.filepath or img.name))[0] or img.name
            if role == "stage-opacity":
                name += ".OPC"
            self.tex_cache[key] = XTex("textures\\%s.dds" % name, arr, kind, no_swap=(role == "normal"))
        return self.tex_cache[key]

    def material(self, mat, uv_anim_ok):
        if mat is None:
            return self._default_material()
        if mat.name in self.mat_cache:
            return self.mat_cache[mat.name]
        xm = XMat(mat.name)
        p = mat.mua
        xm.global_color = p.global_color_enabled if p.global_color_present else None
        if p.active:
            xm.alpha_test, xm.alpha_func, xm.alpha_ref = p.alpha_test, int(p.alpha_func), p.alpha_ref
            depth = mat.original.mua
            if depth.material_mode_present:
                xm.material_mode = depth.material_mode_value if depth.material_mode == "CUSTOM" else int(depth.material_mode)
            xm.depth_write = bool(int(depth.depth_write)) if depth.depth_write_present else None
            xm.texture_matrix_enabled = p.texture_matrix
            xm.texture_matrix_present = p.texture_matrix_present
            xm.stage_attrs = {}
            for stage in depth.texture_stages:
                for kind, fields in (("coord", ("coord_mode", "coord_index")),
                                     ("function", ("mode","arg0","arg1","stage_type","call_state","function_type"))):
                    if getattr(stage, kind):
                        key = (stage.unit, kind)
                        if key in xm.stage_attrs:
                            raise ValueError(f"{mat.name}: duplicate {kind} attribute on unit {stage.unit}")
                        xm.stage_attrs[key] = tuple(getattr(stage, field) for field in fields)
            xm.cube_apply = depth.cube_apply
            xm.vertex_blend = depth.vertex_blend_enabled if depth.vertex_blend_present else None
            xm.global_color = depth.global_color_enabled if depth.global_color_present else None
            xm.shader_parameters_enabled = p.shader_parameters
            xm.blend = int(p.blend_type)
            xm.lighting = bool(p.lighting)
            xm.shininess = float(p.shininess)
            xm.diffuse, xm.ambient = tuple(p.diffuse), tuple(p.ambient)
            xm.specular, xm.emission = tuple(p.specular), tuple(p.emission)
            xm.reflectance, xm.transparent = tuple(p.reflectance), bool(p.transparent)
            xm.diffuse_tex = self.tex(p.diffuse_tex, "dxt5" if xm.blend or p.alpha_test else "dxt1")
            if p.stage1_image:
                role = "stage-opacity" if p.stage1_image.get("mua_opacity_stage") else "stage-color"
                xm.normal_tex = self.tex(p.stage1_image, "dxt5", role)
            else:
                xm.normal_tex = self.tex(p.normal_tex, "dxt5", "normal")
            xm.specular_tex = self.tex(p.specular_tex, "dxt1")
            xm.emissive_tex = self.tex(p.emissive_tex, "dxt1")
            xm.reflection_mask = self.tex(p.reflection_mask, "dxt1")
            faces = bio.reflection_images(p)
            if p.reflection_mode == "SPHERE":
                xm.reflection_sphere = self.tex(p.reflection_all, "rgba8")
            elif any(not _is_ph(f) for f in faces):
                xs = []
                sizes = {tuple(f.size) for f in faces if not _is_ph(f)}
                side = next(iter(sizes))[0] if len(sizes) == 1 and all(w == h for w, h in sizes) else 128
                for f in faces:
                    if _is_ph(f):
                        xs.append(XTex("env.png", np.zeros((side, side, 4), np.float32), "rgba8"))
                    else:
                        key = (id(f), "rgba8", "face", side)
                        if key not in self.tex_cache:
                            self.tex_cache[key] = XTex(os.path.basename(f.filepath or f.name) or "env.png",
                                                       _resize(bio.image_array(f), side), "rgba8")
                        xs.append(self.tex_cache[key])
                xm.reflection_faces = xs
                xm.cube_fallback = self.tex(p.cube_fallback, "rgba8")
        else:                                     # foreign material: first image feeding Base Color / any image
            node = None
            if mat.use_nodes and mat.node_tree:
                node = next((n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image), None)
            if node is not None:
                xm.diffuse_tex = self.tex(node.image, "dxt1")
            self.warnings.append("material %s is not an Alchemy material: exported with defaults + its first image" % mat.name)
        if uv_anim_ok:
            xm.uv_anim = self.uv_anim(mat)
        self.mat_cache[mat.name] = xm
        return xm

    def _default_material(self):
        if "__default__" not in self.mat_cache:
            self.mat_cache["__default__"] = XMat("default")
            self.mat_cache["__default__"].global_color = self.o.mode == "SKIN" or None
        return self.mat_cache["__default__"]

    def uv_anim(self, mat):
        nt = mat.node_tree
        if nt is None or nt.animation_data is None or nt.animation_data.action is None:
            return None
        act = nt.animation_data.action
        path = 'nodes["UVMapping"].inputs[1].default_value'
        curves = {fc.array_index: fc for fc in act.fcurves if fc.data_path == path}
        if 0 not in curves and 1 not in curves:
            return None
        scene = bpy.context.scene
        fps = scene.render.fps / scene.render.fps_base
        start, end = scene.frame_start, scene.frame_end
        intervals = max(1,int(np.ceil((end-start)/fps*(self.o.sample_rate or fps))))
        frames = list(np.linspace(start,end,intervals+1)) if end > start else [float(start)]
        if self.o.force_sample and end > start:
            frames = list(np.linspace(start, end, int(self.o.intervals) + 1))
        t0 = frames[0]
        times = np.array([(f - t0) / fps for f in frames], np.float64)
        trans = np.zeros((len(frames), 3))
        for i in (0, 1):
            if i in curves:
                trans[:, i] = [curves[i].evaluate(f) for f in frames]
        trans[:, 1] *= -1.0                       # back to D3D (v down)
        return {"times": times, "trans": trans, "type": ANIM_TYPE_ENUM.get(self.o.anim_type, 0)}


# ----------------------------------------------------------------------- meshes
def mesh_objects(context, o, arm_obj):
    return [ob for ob in context.scene.objects if ob.type == "MESH"]



def vertex_weights(obj, bone_index):
    """(nv,4) bone indices (-1 unused) and weights from the object's vertex groups."""
    mesh = obj.data
    names = {g.index: g.name for g in obj.vertex_groups}
    nv = len(mesh.vertices)
    J = np.full((nv, 4), -1, np.int32)
    W = np.zeros((nv, 4), np.float32)
    for v in mesh.vertices:
        gs = [(g.weight, bone_index[names[g.group]]) for g in v.groups
              if g.group in names and names[g.group] in bone_index and g.weight > 1e-4]
        gs.sort(reverse=True)
        for k, (w, bi) in enumerate(gs[:4]):
            J[v.index, k], W[v.index, k] = bi, w
    s = W.sum(1, keepdims=True)
    W = np.divide(W, s, out=np.zeros_like(W), where=s > 0)
    return J, W


def collect_object(obj, col, scale, bone_index=None, root_bone=0):
    # Evaluate modelling modifiers and shape keys without baking the active pose.
    modifiers = [(m, m.show_viewport) for m in obj.modifiers if m.type == "ARMATURE"]
    evaluated = None
    try:
        for mod, _ in modifiers:
            mod.show_viewport = False
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return _collect_object(evaluated, col, scale, bone_index, root_bone)
    finally:
        for mod, visible in modifiers:
            mod.show_viewport = visible
        bpy.context.view_layer.update()


def _collect_object(obj, col, scale, bone_index=None, root_bone=0):
    """One Blender mesh object -> list of XMesh (one per material slot used)."""
    mesh = obj.data
    mesh.calc_loop_triangles()
    nt = len(mesh.loop_triangles)
    if nt == 0:
        return []
    tl = np.empty(nt * 3, np.int32)
    mesh.loop_triangles.foreach_get("loops", tl)
    tp = np.empty(nt, np.int32)
    mesh.loop_triangles.foreach_get("polygon_index", tp)
    nl = len(mesh.loops)
    lv = np.empty(nl, np.int32)
    mesh.loops.foreach_get("vertex_index", lv)
    co = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    cn = np.empty(nl * 3, np.float32)
    mesh.corner_normals.foreach_get("vector", cn)
    cn = cn.reshape(-1, 3).astype(np.float64)
    uvl = mesh.uv_layers.active
    uv = np.zeros((nl, 2), np.float64)
    if uvl is not None:
        a = np.empty(nl * 2, np.float32)
        uvl.data.foreach_get("uv", a)
        uv = a.reshape(-1, 2).astype(np.float64)
    uv[:, 1] = 1.0 - uv[:, 1]                                  # Blender v is up, the file's is down
    M = np.array(obj.matrix_world)
    M3, t = M[:3, :3], M[:3, 3]
    pos = (co @ M3.T + t) / scale
    nrm = cn @ np.linalg.inv(M3)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    flip = np.linalg.det(M3) < 0
    pm = np.empty(len(mesh.polygons), np.int32)
    mesh.polygons.foreach_get("material_index", pm)
    tri_mat = pm[tp]
    tris = tl.reshape(nt, 3)
    if bone_index is not None:
        J, W = vertex_weights(obj, bone_index)
        none = W.sum(1) <= 0
        if none.any():
            J[none, 0], W[none, 0] = root_bone, 1.0
            col.warnings.append("%s: %d unweighted vertices bound to the root bone" % (obj.name, int(none.sum())))
    out = []
    p = obj.original.mua
    color_attr = mesh.color_attributes.active_color if p.vertex_colors else None
    colors = None
    if color_attr is not None:
        colors = np.array([tuple(x.color) for x in color_attr.data], np.float32)
        if color_attr.domain == "POINT":
            colors = colors[lv]
    for mi in sorted(set(tri_mat.tolist())):
        sel = tris[tri_mat == mi]
        loops = sel.reshape(-1)
        v = lv[loops]
        parts = [pos[v], nrm[loops], uv[loops]]
        if colors is not None:
            parts.append(colors[loops])
        if bone_index is not None:
            parts += [J[v].astype(np.float64), W[v].astype(np.float64)]
        inv, first = _weld(parts)
        faces = inv.reshape(-1, 3)
        if flip:
            faces = faces[:, [0, 2, 1]]
        mat = obj.material_slots[mi].material if mi < len(obj.material_slots) else None
        xm = col.material(mat, col.o.export_animation or col.o.mode == "SKIN")
        export_name = obj.get("mua_source_name", obj.name) if obj.name == obj.get("mua_import_object_name") else obj.name
        xmesh = XMesh(export_name, xm, pos[v][first].astype(np.float32), nrm[loops][first].astype(np.float32),
                      uv[loops][first].astype(np.float32), faces,
                      J[v][first] if bone_index is not None else None,
                      W[v][first] if bone_index is not None else None)
        xmesh.cull, xmesh.flags, xmesh.indexed = p.cull, p.node_flags(), bool(p.indexed)
        xmesh.use_normals, xmesh.use_uv, xmesh.use_material = bool(p.normals), bool(p.texture), bool(p.material)
        xmesh.segment = bool(p.segment_node)
        xmesh.segment_name = obj.get("mua_segment_name") or obj.name
        xmesh.colors = colors[loops][first] if colors is not None else None
        if p.instantiation or p.external_object:
            col.warnings.append("%s: Instantiation / External Object are stored but not exported" % obj.name)
        out.append(xmesh)
    return out


# ------------------------------------------------------------------- model export
def export_model(context, path, o):
    scene = context.scene
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    if o.export_animation:
        scene.frame_set(int(o.static_frame))
    try:
        objs = mesh_objects(context, o, None)
        candidate = find_armature(context, objs)
        embedded = bool(candidate and candidate.animation_data and candidate.animation_data.action and o.export_animation)
        arm_obj = candidate if o.mode in ("AUTO", "SKIN") or embedded else None
        if arm_obj is not None:
            objs = mesh_objects(context, o, arm_obj)
        scale = float(arm_obj.get(bio.PROP_SCALE, o.scale)) if (arm_obj is not None and o.auto_scale) else o.scale
        if arm_obj is None and objs and o.auto_scale:
            scale = float(objs[0].get(bio.PROP_SCALE, scale))
        if not objs:
            raise RuntimeError("no mesh objects to export (select them or unhide them)")
        helpers = []
        for helper in context.scene.objects:
            if helper.type != 'EMPTY' or not helper.get('mua_helper'):
                continue
            matrix = np.array(helper.matrix_world, dtype=float)
            matrix[:3,3] /= scale
            helpers.append(dict(name=helper.get('mua_helper_name',helper.name), matrix=matrix.T,
                                flags=int(helper.get('mua_helper_flags',0))))
        col = Collector(o, scale)
        name = os.path.splitext(os.path.basename(path))[0]
        if embedded and (o.mode != "MANNEQUIN" or o.anim_workaround):
            from .embedded_export import collect
            model = collect(context, arm_obj, objs, col, scale, o, name)
            model.generate_mipmaps = getattr(o, "generate_mipmaps", False)
            model.helpers = helpers
            model.generate_hull = getattr(o, "generate_hull", False)
            if o.mode == "MANNEQUIN" and isinstance(model, XSkin):
                from .mannequin import build
                info = build(path, model, o.swap_rb)
            else:
                info = build_skin_file(path, model, o.swap_rb) if isinstance(model, XSkin) else build_static_file(path, model, o.swap_rb)
            kind = "animated model"
        elif arm_obj is not None and o.mode not in ("STATIC", "MANNEQUIN"):
            infos, bone_index = collect_bones(arm_obj, scale, o.normalize_frames)
            root = bone_index.get("Bip01", 0)
            skin = XSkin(name)
            skin.generate_mipmaps = getattr(o, "generate_mipmaps", False)
            skin.helpers = helpers
            skin.bones = xbones(infos, scale)
            for ob in objs:
                skin.meshes += collect_object(ob, col, scale, bone_index, root)
            # Preserve authored geometry and bind frames, as the Max Skin
            # exporter does. Reference-pose rebaking changes the skin itself.
            # Root control alignment below remains independent of limb pose.
            from .bind_axes import repair
            repaired = repair(skin)
            from .root_motion import normalize_actor_root
            col.warnings.extend(normalize_actor_root(skin))
            info = build_skin_file(path, skin, o.swap_rb)
            if repaired:
                info['Repaired joint axes'] = repaired
            if hasattr(skin,'normalized_root_pivot'):
                info['Bip01 bind pivot'] = skin.normalized_root_pivot
            kind = "skin"
        else:
            st = XStatic(name)
            st.generate_mipmaps = getattr(o, "generate_mipmaps", False)
            st.helpers = helpers
            st.generate_hull = getattr(o, "generate_hull", False)
            for ob in objs:
                if o.mode == "MANNEQUIN":
                    st.meshes += _collect_object(ob.evaluated_get(context.evaluated_depsgraph_get()), col, scale, None, 0)
                else:
                    st.meshes += collect_object(ob, col, scale)
            info = build_static_file(path, st, o.swap_rb)
            kind = "static model"
        return kind, info, col.warnings
    finally:
        if o.export_animation:
            scene.frame_set(old_frame, subframe=old_subframe)


# ------------------------------------------------------------- animation export
def sample_pose(context, arm_obj, action, rate, force_n):
    """Evaluate the action: returns (frames_per_sample, rot (n,nb,4 wxyz), loc (n,nb,3)) over pose bones."""
    scene = context.scene
    fps = scene.render.fps / scene.render.fps_base
    f0, f1 = scene.frame_start, scene.frame_end
    dur = (f1 - f0) / fps
    n = (int(force_n) + 1 if force_n else int(np.ceil(dur * rate - 1e-5)) + 1) if dur > 0 else 1
    rate = (n - 1) / dur if dur > 0 else max(rate, 1)
    ad = arm_obj.animation_data_create()
    old_action, old_frame, old_subframe = ad.action, scene.frame_current, scene.frame_subframe
    ad.action = action
    pbs = list(arm_obj.pose.bones)
    rot = np.zeros((n, len(pbs), 4))
    loc = np.zeros((n, len(pbs), 3))
    scales = np.ones((n, len(pbs), 3))
    try:
        for i in range(n):
            f = f0 + i * fps / rate
            scene.frame_set(int(np.floor(f)), subframe=float(f - np.floor(f)))
            evaluated = arm_obj.evaluated_get(context.evaluated_depsgraph_get())
            for j, original in enumerate(pbs):
                pb = evaluated.pose.bones[original.name]
                # Bake the final evaluated pose (IK, constraints and drivers),
                # including compensation for Blender-only inheritance settings.
                rest = pb.bone.matrix_local
                pose = pb.matrix
                if pb.parent is not None:
                    rest = pb.parent.bone.matrix_local.inverted() @ rest
                    pose = pb.parent.matrix.inverted() @ pose
                m = rest.inverted() @ pose
                direct = np.allclose(np.array(m), np.array(original.matrix_basis), atol=1e-6, rtol=0)
                if direct:
                    m = original.matrix_basis
                q = m.to_quaternion()
                rot[i, j] = (q.w, q.x, q.y, q.z)
                loc[i, j] = np.array(m.to_translation()) * float(arm_obj.matrix_world.to_scale()[0])
                scales[i, j] = tuple(original.scale) if direct else tuple(m.to_scale())
    finally:
        ad.action = old_action
        scene.frame_set(old_frame, subframe=old_subframe)
    return rate, n, pbs, rot, loc, scales


def export_action(context, path, o):
    arm_obj = find_armature(context)
    if arm_obj is None:
        raise RuntimeError("select the armature whose action you want to export")
    ad = arm_obj.animation_data
    if ad is None or ad.action is None:
        raise RuntimeError("the armature has no active action")
    action = ad.action
    scale = float(arm_obj.get(bio.PROP_SCALE, o.scale)) if o.auto_scale else o.scale
    infos, bone_index = collect_bones(arm_obj, scale, o.normalize_frames)
    scene = context.scene
    fps = scene.render.fps / scene.render.fps_base
    rate = int(o.sample_rate) if o.sample_rate > 0 else int(action.get("mua_sample_rate", 0)) or int(round(fps))
    rate, n, pbs, rot, loc, scales = sample_pose(context, arm_obj, action, rate, int(o.intervals) if o.force_sample else 0)
    stem = os.path.splitext(os.path.basename(path))[0]          # database skeleton label; igAnimation uses the Blender Action name
    anim = XAnim(action.name, rate, n)
    anim.playback_type = o.anim_type
    pb_index = {pb.name: j for j, pb in enumerate(pbs)}
    for i, info in enumerate(infos):
        j = pb_index.get(info.name)
        if j is None:
            continue
        parent = infos[info.parent] if info.parent >= 0 else None
        translated = True
        q, t = P.anim_from_basis(rot[:, j, :], loc[:, j, :] if translated else None, info.J,
                                 parent.J if parent else None, info.head, parent.head if parent else None,
                                 scale, info.D, parent.D if parent else None)
        anim.tracks[info.name] = (q, t)
        if np.any(scales[:, j, :] != 1):
            anim.scales[info.name] = scales[:, j, :] @ P.C.T
    source_rest = json.loads(action.get("mua_source_rest_trans", "{}"))
    object_scale = float(arm_obj.matrix_world.to_scale()[0])
    # Literal export retains every sampled translation, including rest constants.
    unbound_motion = json.loads(action.get("mua_unbound_motion", "null"))
    if unbound_motion and "Motion" not in anim.tracks:
        times = np.linspace(0, anim.duration, n) + (scene.frame_start - float(action.get("mua_source_start_frame", scene.frame_start))) / fps
        q = P._resample(np.asarray(unbound_motion["times"]), np.asarray(unbound_motion["quats"]), times, True)
        t = (np.tile(np.asarray(source_rest.get("Motion", [0,0,0]),float)*object_scale, (n,1)) if unbound_motion["trans"] is None else P._resample(np.asarray(unbound_motion["times"]), np.asarray(unbound_motion["trans"]), times, False) * object_scale)
        anim.tracks["Motion"] = (q,t)
    source_motion = action.get("mua_motion_velocity")
    extract_motion = bool(getattr(o, "extract_motion", False))
    if extract_motion:
        if "Motion" not in anim.tracks:
            raise ValueError("Extract Motion requires a Motion bone")
        qm, tm = anim.tracks["Motion"]
        if tm is None:
            tm = np.zeros((n, 3), dtype=np.float64)
        delta = tm[-1] - tm[0]
        # A new animation may put locomotion on Bip01 instead of Motion.
        if extract_motion and np.linalg.norm(delta[:2]) < 1e-8 and "Bip01" in anim.tracks:
            qb, tb = anim.tracks["Bip01"]
            if tb is not None:
                travel = np.linspace(np.zeros(3), tb[-1] - tb[0], n)
                travel[:, 2] = 0
                tm = tm + travel
                anim.tracks["Bip01"] = (qb, tb - travel)
        anim.tracks["Motion"] = (qm, tm)
        velocity = (tm[-1]-tm[0])/anim.duration if anim.duration > 0 else np.zeros(3)
        velocity[2] = 0  # Engine locomotion is horizontal; vertical motion remains keyed.
        anim.motion_velocity = tuple(velocity) + (-999.0,)
        if source_motion is not None:
            object_scale = float(arm_obj.matrix_world.to_scale()[0])
            source_velocity = np.asarray(source_motion[:3]) * object_scale
            if np.allclose(velocity, source_velocity, atol=1e-4, rtol=0):
                anim.motion_velocity = tuple(source_velocity) + (-999.0,)
            anim.motion_offset = tuple(np.asarray(action.get("mua_motion_offset", (0,0,0))) * object_scale)
    bones = xbones(infos, scale)
    if unbound_motion and "Motion" not in bone_index:
        bones.append(XBone("Motion", -1, source_rest.get("Motion", [0,0,0]), np.eye(3)))
    if getattr(o,"remove_pos",False):
        from .remove_pos import apply
        apply(anim)
    fmt = o.track_format
    if fmt == "SEQUENCE" and o.remove_pos and getattr(o, "post_compress", False):
        fmt = "POST_ENBAYA"
    info = build_anim_file(path, anim, bones, stem, fmt)
    return info, action.name
