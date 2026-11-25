"""Blender side of the MUA IGB importer (requires bpy)."""
import contextlib
import json
import os
import numpy as np
import bpy
from mathutils import Matrix, Vector

from . import pose_math as P
from .mua_model import load_model, load_models
from .mua_anim import load_animations, Anim, Track

PROP_SCALE = "mua_igb_scale"
SHADER_NAME = "MarvelStandart"
_SYNC_LOCK = [0]
@contextlib.contextmanager
def sync_suspended():
    """Property update callbacks do nothing inside this block (used while the importer fills properties)."""
    _SYNC_LOCK[0] += 1
    try:
        yield
    finally:
        _SYNC_LOCK[0] -= 1


# ----------------------------------------------------------------- textures
class Ctx:
    """Per-import state: caches (textures/materials are shared between mesh pieces) + options."""
    def __init__(self, folder, flip_green, report, scale):
        self.folder, self.flip_green, self.report, self.scale = folder, flip_green, report, scale
        self.images, self.materials, self.used_names = {}, {}, set()
        self.outline_coll = None


def convert_normal(rgba, flip_green):
    """MUA DXT5nm-style map (X in alpha, Y in green) -> normal RGB for Blender."""
    x = rgba[..., 3] * 2.0 - 1.0
    y = rgba[..., 1] * 2.0 - 1.0
    z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
    if flip_green:           # DirectX (Y down) -> OpenGL (Y up)
        y = -y
    out = np.empty_like(rgba)
    out[..., 0], out[..., 1], out[..., 2], out[..., 3] = x * .5 + .5, y * .5 + .5, z * .5 + .5, 1.0
    return out


def _unique(ctx, name):
    n, i = name, 1
    while n.lower() in ctx.used_names:
        i += 1
        n = "%s_%d" % (name, i)
    ctx.used_names.add(n.lower())
    return n


class _Tex:
    """Minimal texture-like object for get_image()."""
    def __init__(self, rgba, name, key):
        self.rgba, self.name, self.key = rgba, name, key


def get_image(ctx, tex, variant):
    """variant: 'color' (sRGB), 'data' (Non-Color) or 'normal' (converted, Non-Color)."""
    key = (tex.key, variant)
    if key in ctx.images:
        return ctx.images[key]
    rgba = convert_normal(tex.rgba, ctx.flip_green) if variant == "normal" else tex.rgba
    stem = os.path.splitext(tex.name or "texture%d" % tex.key)[0]
    name = _unique(ctx, stem + ("_normal" if variant == "normal" else ""))
    h, w = rgba.shape[:2]
    img = bpy.data.images.new(name, w, h, alpha=True)
    img.colorspace_settings.name = "sRGB" if variant == "color" else "Non-Color"
    img.pixels.foreach_set(np.ascontiguousarray(rgba[::-1], np.float32).ravel())   # Blender is bottom-up
    img.update()
    saved = None
    if ctx.folder:
        try:
            os.makedirs(ctx.folder, exist_ok=True)
            path = os.path.join(ctx.folder, name + ".png")
            img.filepath_raw = path
            img.file_format = "PNG"
            img.save()
            bpy.data.images.remove(img)
            img = bpy.data.images.load(path, check_existing=False)
            saved = path
        except Exception as e:
            ctx.report("note: could not save %s.png (%s); packing into the .blend instead" % (name, e))
            img = bpy.data.images.get(name) or img
    if saved is None:
        try:
            img.pack()
        except Exception:
            pass
    img.colorspace_settings.name = "sRGB" if variant == "color" else "Non-Color"
    ctx.images[key] = img
    return img


# ------------------------------------------------------------- cube map preview
def cube_to_equirect(faces, width=256):
    """Six face arrays (+X,-X,+Y,-Y,+Z,-Z; any may be None) -> equirectangular float32 RGBA for Blender's
    Environment Texture. The face orientation is an assumption: this is a preview only (the export uses
    the six images as they are)."""
    h = width // 2
    u = (np.arange(width) + 0.5) / width
    v = (np.arange(h) + 0.5) / h
    phi = (u - 0.5) * 2 * np.pi
    theta = (v - 0.5) * np.pi
    PH, TH = np.meshgrid(phi, theta)
    d = np.stack([-np.cos(TH) * np.cos(PH), np.cos(TH) * np.sin(PH), np.sin(TH)], -1)
    ax = np.abs(d)
    face = np.argmax(ax, -1)
    sign = np.take_along_axis(d, face[..., None], -1)[..., 0] < 0
    idx = face * 2 + sign
    out = np.zeros((h, width, 4), np.float32)
    out[..., 3] = 1.0
    for i, img in enumerate(faces):
        sel = idx == i
        if img is None or not sel.any():
            continue
        dd = d[sel]
        m = np.abs(dd).max(1)
        x, y, z = dd[:, 0] / m, dd[:, 1] / m, dd[:, 2] / m
        a, b = {0: (-y, -z), 1: (y, -z), 2: (x, -z), 3: (-x, -z), 4: (x, y), 5: (x, -y)}[i]
        ih, iw = img.shape[:2]
        px = np.clip(((a + 1) / 2 * (iw - 1)).round().astype(int), 0, iw - 1)
        py = np.clip(((b + 1) / 2 * (ih - 1)).round().astype(int), 0, ih - 1)
        out[sel] = img[py, px]
    return out[::-1]                                        # Blender images are bottom-up


def sphere_to_equirect(img, width=256):
    """Preview a sphere-map image without converting its stored export representation."""
    u = (np.arange(width)+.5)/width
    v = (np.arange(width//2)+.5)/(width//2)
    ph, th = np.meshgrid((u-.5)*2*np.pi, (v-.5)*np.pi)
    x,y,z = -np.cos(th)*np.cos(ph), np.cos(th)*np.sin(ph), np.sin(th)
    den = 2*np.sqrt(x*x+y*y+(z+1)*(z+1))
    den = np.maximum(den,1e-9)
    h,w = img.shape[:2]
    px = np.clip(((x/den+.5)*(w-1)).round().astype(int),0,w-1)
    py = np.clip(((.5-y/den)*(h-1)).round().astype(int),0,h-1)
    return img[py,px][::-1]


def image_array(img):
    """bpy Image -> float32 RGBA (h, w, 4), top row first."""
    w, h = img.size
    if w == 0 or h == 0:
        try:
            img.update()
            w, h = img.size
        except Exception:
            pass
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    return a.reshape(h, w, 4)[::-1].copy()


# ------------------------------------------------------------------ materials
def _socket(node, *names):
    for n in names:
        if n in node.inputs:
            return node.inputs[n]
    return None


def _luma(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _set_blend(mat, on):
    for attr, val in (("surface_render_method", "BLENDED" if on else "DITHERED"),
                      ("blend_method", "BLEND" if on else "OPAQUE")):
        try:
            setattr(mat, attr, val)
            return
        except Exception:
            pass


def blend_type(mesh):
    """MarvelStandart BlendType: 0 off, 1 normal (src a, 1-src a), 2 additive (src a, one), 3 (one, 1-src a)."""
    if not mesh.blend:
        return 0
    bf = mesh.blend_func
    if bf is None or tuple(bf) == (4, 5):
        return 1
    if tuple(bf) == (4, 1):
        return 2
    if tuple(bf) == (1, 5):
        return 3
    return 1


def _node(nt, name):
    return nt.nodes.get(name)


def _rewire(nt, out_socket, in_socket):
    for l in list(in_socket.links):
        nt.links.remove(l)
    if out_socket is not None:
        nt.links.new(out_socket, in_socket)


def sync_material(mat):
    """Push the material's Alchemy properties (mat.mua) into its shader nodes."""
    if _SYNC_LOCK[0] or mat is None or mat.node_tree is None:
        return
    p = mat.mua
    nt = mat.node_tree
    N = nt.nodes
    bsdf = next((n for n in N if n.type == "BSDF_PRINCIPLED"), None)
    out = next((n for n in N if n.type == "OUTPUT_MATERIAL"), None)
    if bsdf is None or out is None or N.get("DiffuseColor") is None:
        return                                   # not a MUA-built node tree

    # Upgrade node names in .blend files created by 0.7.0 without replacing UV animation.
    if N.get("MUA Normal") is None:
        legacy = next((n for n in N if n.type == "NORMAL_MAP"), None)
        if legacy is not None:
            legacy.name = "MUA Normal"
    if N.get("MUA Reflection Vector") is None:
        legacy = next((n for n in N if n.type == "VECT_MATH" and n.operation == "REFLECT"), None)
        if legacy is not None:
            legacy.name = "MUA Reflection Vector"

    def texture(name, img, targets, location):
        node = N.get(name)
        if img is None:
            if node is not None:
                N.remove(node)
        else:
            if node is None:
                node = N.new("ShaderNodeTexImage")
                node.name, node.label, node.location = name, name, location
            node.image = img
            if name == "DiffuseTexture":
                mapping = N.get("UVMapping")
                if mapping is not None:
                    _rewire(nt, mapping.outputs["Vector"], node.inputs["Vector"])
        for target, socket, output, default in targets:
            target_node = N.get(target)
            if target_node is not None:
                inp = target_node.inputs[socket]
                _rewire(nt, node.outputs[output] if img is not None else None, inp)
                inp.default_value = default
        return node if img is not None else None

    texture("DiffuseTexture", p.diffuse_tex, [("DiffuseColor",1,"Color",(1,1,1,1)), ("OpacityMul",0,"Alpha",1)], (-1100,600))
    normal = texture("NormalMapTexture", p.normal_tex, [("MUA Normal", "Color", "Color", (.5,.5,1,1))], (-1100,300))
    nm = N.get("MUA Normal")
    _rewire(nt, nm.outputs["Normal"] if normal is not None else None, bsdf.inputs["Normal"])
    texture("SpecularTexture", p.specular_tex, [("SpecularBW","Color","Color",(0,0,0,1))], (-1100,0))
    texture("EmissiveTexture", p.emissive_tex, [("Emission sum",1,"Color",(0,0,0,1))], (-1100,-300))
    texture("ReflectionMaskTexture", p.reflection_mask, [("Reflection x mask",2,"Color",(1,1,1,1))], (-1100,-900))
    faces = reflection_images(p)
    env = N.get("ReflectionTexture")
    if any(f is not None for f in faces):
        if env is None:
            env = N.new("ShaderNodeTexEnvironment")
            env.name, env.location = "ReflectionTexture", (-1100,-600)
        arrs = [image_array(f) if f is not None else None for f in faces]
        eq = sphere_to_equirect(arrs[0]) if p.reflection_mode == "SPHERE" else cube_to_equirect(arrs)
        name = "MUA_env_" + mat.name
        img = bpy.data.images.get(name)
        if img is None or tuple(img.size) != (eq.shape[1], eq.shape[0]):
            if img is not None:
                bpy.data.images.remove(img)
            img = bpy.data.images.new(name, eq.shape[1], eq.shape[0], alpha=True)
        img.pixels.foreach_set(np.ascontiguousarray(eq, np.float32).ravel())
        img.update()
        img.pack()
        env.image = img
        _rewire(nt, N["MUA Reflection Vector"].outputs[0], env.inputs["Vector"])
        _rewire(nt, env.outputs["Color"], N["Reflection x mask"].inputs[1])
    else:
        if env is not None:
            N.remove(env)
        N["Reflection x mask"].inputs[1].default_value = (0,0,0,1)
    mapping = N.get("UVMapping")
    if mapping is not None:
        mapping.mute = not p.texture_matrix
    # colours / scalars
    dcol = N.get("DiffuseColor")
    tint = np.array(p.diffuse[:3], float)
    if dcol is not None:
        dcol.inputs[2].default_value = (tint[0], tint[1], tint[2], 1.0)
    mat.diffuse_color = (float(tint[0]), float(tint[1]), float(tint[2]), 1.0)
    ec = N.get("EmissiveColor")
    if ec is not None:
        e = p.emission
        ec.outputs[0].default_value = (e[0], e[1], e[2], 1.0)
    if ec is not None:
        modulation = N.get("Emission texture modulation")
        diffuse_node = N.get("DiffuseTexture")
        if mat.get("mua_modulate_emission", False) and diffuse_node is not None and p.emissive_tex is None:
            if modulation is None:
                modulation = N.new("ShaderNodeMixRGB")
                modulation.name = "Emission texture modulation"
                modulation.blend_type = "MULTIPLY"
                modulation.inputs[0].default_value = 1.0
                modulation.location = (-650,-300)
            _rewire(nt, ec.outputs[0], modulation.inputs[1])
            _rewire(nt, diffuse_node.outputs["Color"], modulation.inputs[2])
            _rewire(nt, modulation.outputs[0], N["Emission sum"].inputs[2])
        else:
            if modulation is not None:
                N.remove(modulation)
            _rewire(nt, ec.outputs[0], N["Emission sum"].inputs[2])
    rf = N.get("Reflectance")
    if rf is not None:
        a = float(p.reflectance[0])
        rf.inputs[2].default_value = (a, a, a, 1.0)
    rough = _socket(bsdf, "Roughness")
    if rough is not None:
        s = float(p.shininess)
        rough.default_value = float((2.0 / (s + 2.0)) ** 0.25) if s > 0 else 0.5
    # specular: texture if defined, else the material's specular colour
    spec = _socket(bsdf, "Specular IOR Level", "Specular")
    bw = N.get("SpecularBW")
    if spec is not None:
        if p.specular_tex is None and _luma(p.specular) > 0.001:
            _rewire(nt, None, spec)
            spec.default_value = float(min(1.0, _luma(p.specular)))
        elif bw is not None:
            _rewire(nt, bw.outputs["Val"], spec)
    # lighting / emission
    base = N.get("DiffuseColor")
    ems = N.get("Emission + reflection")
    emi = _socket(bsdf, "Emission Color", "Emission")
    unlit = not p.lighting
    strength = _socket(bsdf, "Emission Strength")
    if strength is not None:
        strength.default_value = 1.0
    if emi is not None:
        src = base.outputs[0] if unlit else (ems.outputs[0] if ems is not None else None)
        _rewire(nt, src, emi)
    if unlit and spec is not None:
        _rewire(nt, None, spec)
        spec.default_value = 0.0
    # Principled exposes diffuse, specular and emission. Ambient stays engine-side.
    mode = p.material_mode if p.material_mode_present else "0"
    vertex = N.get("MUA Material Vertex Color")
    if mode in ("2", "3", "4", "5"):
        if vertex is None:
            vertex = N.new("ShaderNodeVertexColor")
            vertex.name = "MUA Material Vertex Color"
            vertex.location = (-900, 850)
        if mode in ("2", "3"):
            _rewire(nt, vertex.outputs["Color"], dcol.inputs[2])
        elif mode == "4" and spec is not None and not unlit:
            _rewire(nt, vertex.outputs["Color"], spec)
        elif mode == "5":
            mod = N.get("Emission texture modulation")
            if mod is not None:
                _rewire(nt, vertex.outputs["Color"], mod.inputs[1])
            else:
                _rewire(nt, vertex.outputs["Color"], N["Emission sum"].inputs[2])
    if mode not in ("2", "3") and vertex is not None:
        for link in list(dcol.inputs[2].links):
            if link.from_node == vertex:
                nt.links.remove(link)
    if mode not in ("2", "3", "4", "5") and vertex is not None:
        N.remove(vertex)
    # blending
    bt = int(p.blend_type)
    alpha = _socket(bsdf, "Alpha")
    dn = N.get("DiffuseTexture")
    opm = N.get("OpacityMul")
    opacity = float(p.diffuse[3])
    if opm is not None:
        opm.inputs[1].default_value = opacity
        alpha_src = opm.outputs[0]
    else:
        alpha_src = dn.outputs["Alpha"]
    stage_tex = texture("MUA Stage 1 Color", p.stage1_image, [], (-1100,-1200))
    stage_alpha = N.get("MUA Stage Opacity")
    if stage_tex is not None and p.stage1_image.get("mua_opacity_stage", False):
        if stage_alpha is None:
            stage_alpha = N.new("ShaderNodeMath")
            stage_alpha.name, stage_alpha.operation = "MUA Stage Opacity", "MULTIPLY"
        _rewire(nt, alpha_src, stage_alpha.inputs[0])
        _rewire(nt, stage_tex.outputs["Color"], stage_alpha.inputs[1])
        alpha_src = stage_alpha.outputs[0]
    elif stage_alpha is not None:
        N.remove(stage_alpha)
    if p.alpha_test:
        test = N.get("AlphaTest") or N.new("ShaderNodeMath")
        test.name = "AlphaTest"
        func = int(p.alpha_func)
        test.operation = "GREATER_THAN" if func in (3,4) else "COMPARE" if func in (2,5) else "LESS_THAN"
        test.inputs[1].default_value = p.alpha_ref
        if test.operation == "COMPARE":
            test.inputs[2].default_value = 0.0
        _rewire(nt, alpha_src, test.inputs[0])
        alpha_src = test.outputs[0]
        if func in (3,5,6):
            inv = N.get("AlphaTestInverse") or N.new("ShaderNodeMath")
            inv.name, inv.operation = "AlphaTestInverse", "SUBTRACT"
            inv.inputs[0].default_value = 1
            _rewire(nt, alpha_src, inv.inputs[1])
            alpha_src = inv.outputs[0]
        elif func in (0,7):
            test.operation = "MULTIPLY_ADD"
            test.inputs[1].default_value = 0
            test.inputs[2].default_value = 1 if func == 7 else 0
    show_alpha = p.alpha_test or bt in (1, 3) or (bt == 0 and opacity < 0.999)   # opacity below 1 is previewed even without blending
    if show_alpha and bt != 2:
        _rewire(nt, alpha_src, alpha)
        _rewire(nt, bsdf.outputs[0], out.inputs["Surface"])
    elif bt == 2:
        _rewire(nt, None, alpha)
        add = N.get("MUA Additive Add")
        if add is None:
            col = nt.nodes.new("ShaderNodeMixRGB")
            col.name, col.label, col.blend_type = "Additive colour x alpha", "Additive colour x alpha", "MULTIPLY"
            col.inputs[0].default_value = 1.0
            col.location = (100, 200)
            emn = nt.nodes.new("ShaderNodeEmission")
            emn.name, emn.location = "MUA Additive Emission", (300, 200)
            tr = nt.nodes.new("ShaderNodeBsdfTransparent")
            tr.name, tr.location = "MUA Additive Transparent", (300, 0)
            add = nt.nodes.new("ShaderNodeAddShader")
            add.name, add.location = "MUA Additive Add", (500, 100)
            nt.links.new(col.outputs[0], emn.inputs["Color"])
            nt.links.new(emn.outputs[0], add.inputs[0])
            nt.links.new(tr.outputs[0], add.inputs[1])
        col = N.get("Additive colour x alpha")
        _rewire(nt, base.outputs[0], col.inputs[1])
        _rewire(nt, alpha_src, col.inputs[2])
        _rewire(nt, add.outputs[0], out.inputs["Surface"])
    else:
        _rewire(nt, None, alpha)
        _rewire(nt, bsdf.outputs[0], out.inputs["Surface"])
    _set_blend(mat, p.alpha_test or bt != 0 or opacity < 0.999)
    # keep a few values on the material for scripts/exporters that read custom properties
    mat["mua_shader"] = SHADER_NAME


def reflection_images(p):
    """The six direction images (+X,-X,+Y,-Y,+Z,-Z) of a material; one image may serve all directions."""
    if p.reflection_mode == "SPHERE" or p.reflection_same:
        return [p.reflection_all] * 6
    return [p.reflection_px, p.reflection_nx, p.reflection_py, p.reflection_ny, p.reflection_pz, p.reflection_nz]


def create_shader_nodes(mat, uv_anim=None, extra_images=None, report=print):
    """(Re)create the MarvelStandart node layout inside `mat`. Node names are what sync_material looks for."""
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    out_node = nt.nodes.new("ShaderNodeOutputMaterial")
    out_node.location = (700, 300)
    nt.links.new(bsdf.outputs[0], out_node.inputs["Surface"])
    links, nodes = nt.links, nt.nodes
    state = {"y": 600}

    def img_node(cls, name, label):
        n = nodes.new(cls)
        n.name, n.label = name, label
        n.location = (-1100, state["y"])
        state["y"] -= 300
        return n

    def mix(blend, a_out, b_out=None, x=-600, y=0, name=None, b_val=None):
        m = nodes.new("ShaderNodeMixRGB")
        m.blend_type = blend
        m.inputs[0].default_value = 1.0
        if a_out is not None:
            links.new(a_out, m.inputs[1])
        if b_out is not None:
            links.new(b_out, m.inputs[2])
        elif b_val is not None:
            m.inputs[2].default_value = b_val
        m.location = (x, y)
        if name:
            m.name, m.label = name, name
        return m

    dn = img_node("ShaderNodeTexImage", "DiffuseTexture", "Diffuse (unit 0)")
    opm = nodes.new("ShaderNodeMath")
    opm.name, opm.label, opm.operation = "OpacityMul", "Opacity (diffuse alpha)", "MULTIPLY"
    opm.inputs[1].default_value = 1.0
    opm.location = (-700, 900)
    links.new(dn.outputs["Alpha"], opm.inputs[0])
    if uv_anim is not None:
        _add_uv_mapping(report, mat, nt, dn, uv_anim)
    dcol = mix("MULTIPLY", dn.outputs["Color"], x=-700, y=600, name="DiffuseColor", b_val=(1, 1, 1, 1))
    links.new(dcol.outputs[0], bsdf.inputs["Base Color"])
    nn = img_node("ShaderNodeTexImage", "NormalMapTexture", "Normal (unit 1)")
    nm = nodes.new("ShaderNodeNormalMap")
    nm.name = "MUA Normal"
    nm.location = (-700, 300)
    links.new(nn.outputs["Color"], nm.inputs["Color"])
    links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    sn = img_node("ShaderNodeTexImage", "SpecularTexture", "Specular (unit 2)")
    bw = nodes.new("ShaderNodeRGBToBW")
    bw.name = "SpecularBW"
    bw.location = (-700, 0)
    links.new(sn.outputs["Color"], bw.inputs["Color"])
    en = img_node("ShaderNodeTexImage", "EmissiveTexture", "Emissive (unit 5)")
    ecol = nodes.new("ShaderNodeRGB")
    ecol.name, ecol.label = "EmissiveColor", "EmissiveColor"
    ecol.location = (-1100, state["y"])
    state["y"] -= 200
    em_sum = mix("ADD", en.outputs["Color"], ecol.outputs[0], x=-400, y=-200, name="Emission sum")
    rn = img_node("ShaderNodeTexEnvironment", "ReflectionTexture", "Reflection cube map (unit 3)")
    mn = img_node("ShaderNodeTexImage", "ReflectionMaskTexture", "Reflection mask (unit 4)")
    em_out = em_sum.outputs[0]
    try:
        geo = nodes.new("ShaderNodeNewGeometry")
        neg = nodes.new("ShaderNodeVectorMath")
        neg.operation = "SCALE"
        neg.inputs["Scale"].default_value = -1.0
        refl_v = nodes.new("ShaderNodeVectorMath")
        refl_v.name = "MUA Reflection Vector"
        refl_v.operation = "REFLECT"
        links.new(geo.outputs["Incoming"], neg.inputs[0])
        links.new(neg.outputs[0], refl_v.inputs[0])
        links.new(geo.outputs["Normal"], refl_v.inputs[1])
        links.new(refl_v.outputs[0], rn.inputs["Vector"])
        r_out = mix("MULTIPLY", rn.outputs["Color"], mn.outputs["Color"], x=-400, y=-400, name="Reflection x mask").outputs[0]
        r_out = mix("MULTIPLY", r_out, x=-250, y=-400, name="Reflectance", b_val=(1, 1, 1, 1)).outputs[0]
        em_out = mix("ADD", em_out, r_out, x=-100, y=-300, name="Emission + reflection").outputs[0]
    except Exception as e:
        report("note: reflection setup skipped for %s (%s)" % (mat.name, e))
    for u, image in sorted((extra_images or {}).items()):     # unknown units (6-8): kept, unconnected
        n = nodes.new("ShaderNodeTexImage")
        n.name, n.label = "Unit%dTexture" % u, "unit %d (unconnected)" % u
        n.image = image
        n.location = (-1100, state["y"])
        state["y"] -= 300
    nodes.active = dn                               # drives Solid-mode "Texture" colouring too
    sync_material(mat)


def rebuild_nodes(mat):
    create_shader_nodes(mat)
    sync_material(mat)


def build_material(ctx, mesh, model_name):
    """Create Alchemy properties and nodes only for the textures actually assigned."""
    if mesh.material_key in ctx.materials:
        return ctx.materials[mesh.material_key]
    mat = bpy.data.materials.new("%s_%s" % (model_name, mesh.name))
    ctx.materials[mesh.material_key] = mat
    mat.use_nodes = True
    nt = mat.node_tree
    md = mesh.material or {}
    tex = {u: t for u, t in mesh.textures.items() if t.rgba is not None}
    for u, t in mesh.textures.items():
        if t.rgba is None:
            ctx.report("warning: unit %d texture %s not decoded (%s)" % (u, t.name, t.kind))
    diffuse = tuple(md.get("diffuse") or (1.0, 1.0, 1.0, 1.0))
    if mesh.color is not None:
        diffuse = tuple(float(a) * float(b) for a, b in zip(diffuse, mesh.color))
    tint_max = max(diffuse[:3])
    p = mat.mua
    # Legacy fixed-function material emission is modulated by the bound diffuse
    # texture; keep its stored value while reproducing that preview behavior.
    mat["mua_modulate_emission"] = not bool(mesh.params)
    with sync_suspended():
        p.alpha_mode = "4" if mesh.alpha_func == 4 else "6"
        p.alpha_test, p.alpha_func, p.alpha_ref = mesh.alpha_test, str(mesh.alpha_func), mesh.alpha_ref
        stages = {}
        for (unit, kind), values in sorted(getattr(mesh, "stage_attrs", {}).items()):
            if unit not in stages:
                stages[unit] = p.texture_stages.add()
                stages[unit].unit = unit
            stage = stages[unit]
            setattr(stage, kind, True)
            if kind == "coord":
                stage.coord_mode, stage.coord_index = values
            else:
                stage.mode, stage.arg0, stage.arg1, stage.stage_type, stage.call_state, stage.function_type = values
        for key in ("global_color", "vertex_blend"):
            value = getattr(mesh, key, None)
            setattr(p, key + "_present", value is not None)
            if value is not None:
                setattr(p, key + "_enabled", value)
        mode = getattr(mesh, "material_mode", None)
        p.material_mode_present = mode is not None
        if mode is not None:
            p.material_mode = str(mode) if mode in range(6) else "CUSTOM"
            if mode not in range(6):
                p.material_mode_value = mode
        p.depth_write_present = getattr(mesh,"depth_write",None) is not None
        p.depth_write = "0" if getattr(mesh,"depth_write",None) is False else "1"
        states = getattr(mesh,"texture_matrix_states",{})
        p.texture_matrix = states.get(0, bool(mesh.uv_anim))
        p.texture_matrix_present = 0 in states
        p.shader_parameters = any(k in mesh.params for k in ("material.reflectance","material.transparent")) or 3 in tex
        p.active = True
        p.shader_type = SHADER_NAME
        p.blend_type = str(blend_type(mesh))
        p.lighting = mesh.lighting is not False
        p.shininess = float(md.get("shininess") or 0.0)
        if tint_max <= 0.05 and 0 in tex:              # near-black tint (XML2 style) would hide the texture
            p.original_diffuse = diffuse
            diffuse = (1.0, 1.0, 1.0, diffuse[3])
        p.diffuse = diffuse
        p.ambient = tuple(md.get("ambient") or (1.0, 1.0, 1.0, 1.0))
        p.specular = tuple(md.get("specular") or (0.0, 0.0, 0.0, 0.0))
        p.emission = tuple(md.get("emission") or (0.0, 0.0, 0.0, 0.0))
        refl = mesh.params.get("material.reflectance") if mesh.params else None
        p.reflectance = tuple(float(x) for x in refl) if refl is not None and hasattr(refl, "__len__") else (1.0, 0.0, 0.0, 0.0)
        tp = mesh.params.get("material.transparent") if mesh.params else None
        p.transparent = bool(tp) if tp is not None else False
        if 0 in tex:
            p.diffuse_tex = get_image(ctx, tex[0], "color")
        if 1 in tex:
            if 1 in stages and stages[1].function:
                source = tex[1]
                if getattr(mesh, "swap_stage_color", False):
                    source = _Tex(source.rgba[..., [2,1,0,3]], source.name, (source.key, "swapped"))
                p.stage1_image = get_image(ctx, source, "color")
                if source.name.lower().endswith((".opc", ".opc.dds")):
                    p.stage1_image["mua_opacity_stage"] = True
            else:
                p.normal_tex = get_image(ctx, tex[1], "normal")
        if 2 in tex:
            p.specular_tex = get_image(ctx, tex[2], "data")
        if 5 in tex:
            p.emissive_tex = get_image(ctx, tex[5], "color")
        if 4 in tex:
            p.reflection_mask = get_image(ctx, tex[4], "data")
        cube = tex.get(3)
        if cube is not None:
            p.cube_apply = getattr(cube, "cube_apply", -1)
            p.reflection_mode = "CUBE" if cube.faces else "SPHERE"
            if cube.faces:
                if cube.rgba is not None:
                    p.cube_fallback = get_image(ctx, cube, "color")
                imgs = [get_image(ctx, _Tex(f[0], f[1], f[2]), "color") for f in cube.faces]
                p.reflection_same = False
                p.reflection_px, p.reflection_nx, p.reflection_py = imgs[0], imgs[1], imgs[2]
                p.reflection_ny, p.reflection_pz, p.reflection_nz = imgs[3], imgs[4], imgs[5]
            else:
                p.reflection_same = True
                p.reflection_all = get_image(ctx, cube, "color")
    create_shader_nodes(mat, mesh.uv_anim, {u: get_image(ctx, tex[u], "data") for u in sorted(tex) if u > 5}, ctx.report)
    mat.use_backface_culling = bool(mesh.cull)
    sync_material(mat)
    ctx.report("info: material %s -> units %s | BlendType %s%s" % (
        mat.name, ",".join(str(u) for u in sorted(tex)) or "none", p.blend_type, ", unlit" if not p.lighting else ""))
    return mat


def _add_uv_mapping(report, mat, nt, tex_node_, uv):
    """Animated/static texture-matrix transform -> Mapping node (+ keyframes on its Location)."""
    nodes, links = nt.nodes, nt.links
    tc = nodes.new("ShaderNodeTexCoord")
    tc.location = (-1500, 600)
    mp = nodes.new("ShaderNodeMapping")
    mp.name, mp.label = "UVMapping", "UV scroll (texture matrix)"
    mp.location = (-1300, 600)
    links.new(tc.outputs["UV"], mp.inputs["Vector"])
    links.new(mp.outputs["Vector"], tex_node_.inputs["Vector"])
    M = uv["matrix"]
    data = uv.get("data")
    tx, ty = float(M[3, 0]), float(M[3, 1])
    mp.inputs["Location"].default_value = (tx, -ty, 0.0)       # D3D v is down, Blender v is up
    if data is None or len(data["times"]) < 2 or data["trans"] is None:
        return
    scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base
    frames = data["times"] * fps + scene.frame_start
    nt.animation_data_create()
    act = bpy.data.actions.new("%s_uvscroll" % mat.name)
    nt.animation_data.action = act
    path = 'nodes["UVMapping"].inputs[1].default_value'
    vals = (data["trans"][:, 0], -data["trans"][:, 1], np.zeros(len(frames)))
    for i in range(3):
        _write_curve(act, nt, path, i, "UV scroll", frames, vals[i], extrapolation="LINEAR")
    # Blender 4.5 creates the slot with the first FCurve, after action assignment.
    # Explicitly bind that slot so the node tree actually evaluates the curves.
    if act.slots and nt.animation_data.action_slot is None:
        nt.animation_data.action_slot = act.slots[0]
    act.use_fake_user = True
    report("info: %s: animated UV scroll (%d keys)" % (mat.name, len(frames)))


# ----------------------------------------------------------------- armature
def _ensure_object_mode():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def build_armature(model, scale, name):
    _ensure_object_mode()
    arm = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, arm)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = {}
    for b in model.bones:
        e = arm.edit_bones.new(b.name)
        head = b.pos_world * scale
        kids = [c for c in b.children if np.linalg.norm(c.pos_world - b.pos_world) > 1e-4]
        length = (np.linalg.norm(kids[0].pos_world - b.pos_world) * scale) if kids else 2.0 * scale
        length = max(length, 0.5 * scale)
        B3 = b.rot_world @ P.C
        e.head = Vector(head)
        e.tail = Vector(head + B3[:, 1] * length)
        e.matrix = Matrix([[B3[0, 0], B3[0, 1], B3[0, 2], head[0]],
                           [B3[1, 0], B3[1, 1], B3[1, 2], head[1]],
                           [B3[2, 0], B3[2, 1], B3[2, 2], head[2]],
                           [0, 0, 0, 1]])
        eb[b.index] = e
    for b in model.bones:
        if b.parent in eb:
            eb[b.index].parent = eb[b.parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    obj[PROP_SCALE] = scale
    from .native_import import remember
    remember(obj, getattr(model, "native_skeleton_name", None))
    obj.show_in_front = True
    return obj


# --------------------------------------------------------------------- mesh
def _needs_flip(pos, faces, normals):
    """Winding is stored correctly in the files; only flip when the stored normals disagree
    (a signed-volume test is unreliable on open surfaces such as eyes or armour plates)."""
    if normals is None or len(faces) == 0:
        return False
    a, b, c = pos[faces[:, 0]], pos[faces[:, 1]], pos[faces[:, 2]]
    fn = np.cross(b - a, c - a)
    vn = normals[faces].mean(axis=1)
    return (np.einsum("ij,ij->i", fn, vn) > 0).mean() < 0.5


def _outline_collection(ctx, model):
    if ctx.outline_coll is None:
        name = "%s_outlines" % model.name
        coll = bpy.data.collections.get(name) or bpy.data.collections.new(name)
        if coll.name not in bpy.context.scene.collection.children:
            bpy.context.scene.collection.children.link(coll)
        ctx.outline_coll = coll
    return ctx.outline_coll


def build_mesh(ctx, me, model, arm_obj, scale):
    pos = me.positions.astype(np.float64) * scale
    faces = me.faces
    if "outline" not in me.name.lower() and _needs_flip(pos, faces, me.normals):
        faces = faces[:, ::-1]
    mesh = bpy.data.meshes.new(me.name)
    mesh.from_pydata(pos.tolist(), [], faces.tolist())
    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    if me.uv is not None:
        uv = mesh.uv_layers.new(name="UVMap")
        per_loop = me.uv[faces.reshape(-1)].copy()
        per_loop[:, 1] = 1.0 - per_loop[:, 1]                    # D3D (top-left) -> Blender (bottom-left)
        uv.data.foreach_set("uv", per_loop.ravel())
    if getattr(me, "colors", None) is not None:
        colors = mesh.color_attributes.new(name="Color", type="FLOAT_COLOR", domain="POINT")
        colors.data.foreach_set("color", me.colors.ravel())
    mesh.update()
    mesh.validate()
    if me.normals is not None:
        try:
            mesh.normals_split_custom_set_from_vertices(me.normals.tolist())
        except Exception as e:
            ctx.report("note: custom normals not applied (%s)" % e)
    obj = bpy.data.objects.new(me.name, mesh)
    obj[PROP_SCALE] = scale
    obj["mua_source_name"] = me.name
    obj["mua_import_object_name"] = obj.name
    obj.mua.segment_node = bool(me.segment)
    obj["mua_segment_name"] = me.segment or ""
    source_skin = getattr(me, "params", {}).get("source_skin")
    if source_skin:
        obj["mua_source_skin"] = source_skin
        if not hasattr(ctx, "skin_collections"):
            ctx.skin_collections = {}
        if source_skin not in ctx.skin_collections:
            collection = bpy.data.collections.new(source_skin)
            bpy.context.collection.children.link(collection)
            ctx.skin_collections[source_skin] = collection
        ctx.skin_collections[source_skin].objects.link(obj)
    elif "outline" in me.name.lower():            # X-Men Legends cel-shading shell: its own collection
        _outline_collection(ctx, model).objects.link(obj)
        ctx.report("info: %s is a cel-shading outline shell, moved to collection %s" % (obj.name, ctx.outline_coll.name))
    else:
        bpy.context.collection.objects.link(obj)
    mesh.materials.append(build_material(ctx, me, model.name))
    try:
        p = obj.mua
        fl = int(getattr(me, "flags", 0) or 0)
        p.collidable = bool(fl & 0x10)                                   # defaults: collidable and indexed on ...
        p.indexed = bool(me.indexed)
        p.vertex_colors = bool(getattr(me, "has_colors", False))   # ... vertex colours only if the file has them
        p.invisible, p.dynamic_transform, p.dynamic_object = bool(fl & 0x20), bool(fl & 0x40), bool(fl & 0x80)
        p.extra_flags = fl & ~(0x10 | 0x20 | 0x40 | 0x80)
        p.normals, p.texture = me.normals is not None, me.uv is not None
        p.cull = ("DEFAULT" if me.cull is None else "DISABLE" if me.cull is False
                  else "FRONT" if getattr(me, "cull_mode", 0) == 1 else "BACK")
    except Exception as e:
        ctx.report("note: object properties not set (%s)" % e)
    if me.joints is not None and arm_obj is not None:
        bones = {b.index: b.name for b in model.bones}
        groups = {}
        w = me.weights.astype(np.float64)
        s = w.sum(1, keepdims=True)
        w = np.divide(w, s, out=np.zeros_like(w), where=s > 0)   # normalise
        for v in range(len(pos)):
            for k in range(4):
                bi, wt = int(me.joints[v, k]), float(w[v, k])
                if wt <= 1e-6 or bi < 0 or bi not in bones:
                    continue
                nm = bones[bi]
                vg = groups.get(nm) or groups.setdefault(nm, obj.vertex_groups.new(name=nm))
                vg.add([v], wt, "ADD")
        obj.parent = arm_obj
        mod = obj.modifiers.new("Armature", "ARMATURE")
        mod.object = arm_obj
    return obj


def is_root_translation(name, parent):
    """Keep locomotion/pelvis and hierarchy roots; never rely on a limb blacklist."""
    return parent is None or name.casefold().replace("_", " ") in {"bip01", "bip01 pelvis", "motion", "root", "pelvis", "hips"} or (parent or '').casefold() == 'bip01'


def material_signature(mat):
    if mat is None or not mat.mua.active:
        return ("external", mat)
    values = [("emission_modulation",bool(mat.get("mua_modulate_emission",False)))]
    for prop in mat.mua.bl_rna.properties:
        if prop.identifier == "rna_type" or (prop.is_readonly and prop.type != "COLLECTION"):
            continue
        value = getattr(mat.mua, prop.identifier)
        if prop.type == "COLLECTION":
            value = tuple(tuple(getattr(item, field.identifier) for field in item.bl_rna.properties
                                if field.identifier not in ("rna_type", "name")) for item in value)
        if getattr(prop, "is_array", False):
            value = tuple(value)
        values.append((prop.identifier, value))
    # Do not merge independently animated UV materials or unknown textures.
    nt = mat.node_tree
    if nt is not None:
        mapping_node = nt.nodes.get("UVMapping")
        if mapping_node is not None:
            values.append(("UVMapping", tuple(tuple(i.default_value) for i in mapping_node.inputs if i.type == "VECTOR")))
        values.append(("action", nt.animation_data.action if nt.animation_data else None))
        values.extend((n.name,n.image) for n in nt.nodes if n.type == "TEX_IMAGE" and n.name.startswith("Unit"))
    return tuple(values)


def deduplicate_material_slots(ob):
    """Consolidate repeated material references and remap every polygon slot."""
    unique, mapping, keys = [], {}, {}
    for i, mat in enumerate(ob.data.materials):
        signature = material_signature(mat)
        if signature not in keys:
            keys[signature] = len(unique)
            unique.append(mat)
        mapping[i] = keys[signature]
    indices = [mapping.get(poly.material_index, 0) for poly in ob.data.polygons]
    ob.data.materials.clear()
    for mat in unique:
        ob.data.materials.append(mat)
    for poly, index in zip(ob.data.polygons, indices):
        poly.material_index = index


def import_model(path, scale=0.0254, flip_green=False, dxt_mode="AUTO", save_textures=False,
                 import_anim=True, report=print, merge_names=True, _model=None, _anims=None):
    if _model is None:
        models = load_models(path, dxt_mode)
        if len(models)>1:
            animations = load_animations(path, include_static=True) if import_anim else []
            first_arm, all_objects = None, []
            for index, part in enumerate(models):
                collection = bpy.data.collections.new(part.name)
                bpy.context.collection.children.link(collection)
                def owner(clip):
                    matches = []
                    for candidate in models:
                        if candidate.source_skeleton is None:
                            continue
                        stems = {candidate.name.removeprefix('skin_'),
                                 candidate.source_skeleton.removesuffix('_skeleton')}
                        for stem in stems:
                            if clip.name == stem or clip.name.startswith(stem+'_'):
                                matches.append((len(stem),candidate.source_skeleton))
                    if matches:
                        return max(matches)[1]
                    # Shared databases can contain stale named bindings. Compare
                    # the clip's own default skeleton on non-translating channels
                    # before falling back to that binding (e.g. gdp / ptogundam).
                    reference = getattr(clip,'reference_rest',{})
                    moving = {t.bone for t in clip.tracks if t.trans is not None}
                    scores = []
                    for candidate in models:
                        stable = [b for b in candidate.bones if b.name in reference and b.name not in moving]
                        matched = sum(b.name in reference for b in candidate.bones)
                        if stable and matched >= .95*len(candidate.bones):
                            error = float(np.mean([np.linalg.norm(b.trans-reference[b.name])**2 for b in stable]))
                            proxy = 'kage' in candidate.name or 'lod' in candidate.name
                            scores.append((round(error,8),proxy,candidate.source_skeleton))
                    if scores:
                        return min(scores)[2]
                    bound = getattr(clip,'skeleton_names',set())-{'defaultSkeleton'}
                    return next(iter(bound)) if len(bound)==1 else None
                clips = [a for a in animations if owner(a)==part.source_skeleton]
                with bpy.context.temp_override(collection=collection):
                    arm, objects = import_model(path,scale,flip_green,dxt_mode,save_textures,
                                                import_anim,report,merge_names,part,clips)
                if first_arm is None:first_arm=arm
                all_objects.extend(objects)
                if index:
                    collection.hide_viewport = True
                    collection.hide_render = True
            report("info: imported %d independent assets in separate collections; first asset visible" % len(models))
            return first_arm, all_objects
        model = models[0]
    else:
        model = _model
    for w in model.warnings:
        if not (model.embedded_tracks and w == "no geometry found in file"):
            report("warning: " + w)
    for n in model.notes:
        report("note: " + n)
    report("info: DXT colours %s (%s)" % ("R/B swapped (MUA PC convention)" if model.dxt_swapped else "standard", dxt_mode.lower()))
    folder = os.path.join(os.path.dirname(os.path.abspath(path)), "%s_textures" % model.name) if save_textures else None
    ctx = Ctx(folder, flip_green, report, scale)
    arm_obj = build_armature(model, scale, "ActorRoot") if model.bones else None
    objs = [build_mesh(ctx, me, model, arm_obj, scale) for me in model.meshes]
    helpers = {}
    for helper in model.helpers:
        ob = bpy.data.objects.new(helper["name"], None)
        bpy.context.collection.objects.link(ob)
        ob.empty_display_type = 'ARROWS'
        ob.empty_display_size = max(scale*2, 0.01)
        ob["mua_helper"] = True
        ob["mua_helper_name"] = helper["name"]
        ob["mua_helper_flags"] = helper["flags"]
        ob[PROP_SCALE] = scale
        if helper["parent"] in helpers:
            ob.parent = helpers[helper["parent"]]
        matrix = helper["matrix"].T.copy()
        matrix[:3,3] *= scale
        ob.matrix_world = Matrix(matrix.tolist())
        helpers[helper["index"]] = ob

    if merge_names:
        groups = {}
        for source, ob in zip(model.meshes, objs):
            groups.setdefault((source.name, source.segment, tuple(c.name for c in ob.users_collection), material_signature(ob.active_material)), []).append(ob)
        merged = []
        for (name, _, _collections, _material), group in groups.items():
            if len(group) > 1:
                bpy.ops.object.select_all(action='DESELECT')
                for ob in group:
                    ob.select_set(True)
                bpy.context.view_layer.objects.active = group[0]
                bpy.ops.object.join()
            deduplicate_material_slots(group[0])
            group[0].name = name
            group[0]["mua_import_object_name"] = group[0].name
            merged.append(group[0])
        objs = merged
    for obj in objs:
        if obj.get("mua_source_skin", "").lower() in ("skin_shadow", "shadow") or (model.version >= 10 and "shadow" in obj.name.lower()):
            obj.hide_render = True
            obj.hide_set(True)
            obj["mua_shadow_proxy"] = True
    if import_anim and arm_obj is not None and model.embedded_tracks:
        arm_obj["mua_embedded_animation"] = True
        anim = Anim("%s_anim" % model.name, 0.0)
        for bone, d in model.embedded_tracks:
            b = next(b for b in model.bones if b.name == bone)
            parent = next((p for p in model.bones if p.index == b.parent), None)
            local = np.linalg.inv(parent.rot_world) @ b.rot_world if parent else b.rot_world
            bind_scale = np.linalg.norm(local, axis=0)
            sc = d.get("scale")
            sc = np.asarray(sc) / np.maximum(bind_scale, 1e-12) if sc is not None else None
            anim.play_modes[bone] = d.get("play_mode")
            anim.tracks.append(Track(bone, d["times"], d["quats"], d["trans"], sc))
            anim.duration = max(anim.duration, float(d["times"][-1]))
        try:
            _apply_anims(arm_obj, [anim], "ALL", False, False, False, False, scale, report, path)
            report("info: imported embedded animation (%d bone track(s), %.2fs)" % (len(anim.tracks), anim.duration))
        except Exception as e:
            report("note: embedded animation not imported (%s)" % e)
    elif import_anim and arm_obj is not None:
        animations = [a for a in (_anims if _anims is not None else load_animations(path)) if a.tracks and (a.duration > 0 or _anims is not None)]
        if animations:
            arm_obj["mua_embedded_animation"] = True
            actions = _apply_anims(arm_obj, animations, "ALL", False, False, False, False, scale, report, path)
            if _anims is not None:
                arm_obj["mua_import_actions"] = [action.name for action in actions]
            report("info: imported embedded skinned animation (%d action(s))" % len(animations))
    return arm_obj, objs


# ---------------------------------------------------------------- animation
def _new_fcurve(action, id_block, path, index, group):
    try:
        return action.fcurves.new(path, index=index, action_group=group)
    except Exception:
        return action.fcurve_ensure_for_datablock(id_block, path, index=index, group_name=group)


def _write_curve(action, id_block, path, index, group, frames, values, extrapolation=None):
    fc = _new_fcurve(action, id_block, path, index, group)
    n = len(frames)
    fc.keyframe_points.add(n)
    co = np.empty(2 * n, np.float32)
    co[0::2], co[1::2] = frames, values
    fc.keyframe_points.foreach_set("co", co)
    for kp in fc.keyframe_points:
        kp.interpolation = "LINEAR"
    if extrapolation:
        fc.extrapolation = extrapolation
    fc.update()


def armature_rest(arm_obj):
    """bone -> (joint frame J, head (blender units), parent name) from the armature itself."""
    rest = {}
    for b in arm_obj.data.bones:
        B = b.matrix_local
        B3 = np.array([[B[i][j] for j in range(3)] for i in range(3)])
        rest[b.name] = (P.joint_frame_from_blender(B3), np.array(B.translation), b.parent.name if b.parent else None)
    return rest


def _apply_anims(arm_obj, anims, trans_mode, transfer_motion, fit_skeleton, scale_lengths, retarget, scale, report, source_path=None):
    """Write actions against actual rest orientations; fit mode preserves root/pelvis translation."""
    scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base
    if trans_mode == "NONE":
        fit_skeleton = False
    rest = armature_rest(arm_obj)
    if fit_skeleton:
        from .native_import import native_reason
        reason = native_reason(arm_obj, anims, rest)
        if reason:
            report("info: native animation transforms retained: " + reason)
            fit_skeleton = False
            trans_mode = "ALL"
    D = {}
    fitted_rest = {}
    if fit_skeleton:
        from .export_core import XSkin, XBone, rest_local
        from .actor_bind import prepare
        skin = XSkin("Animation preview")
        indices = {name:i for i,name in enumerate(rest)}
        skin.bones = [XBone(name, indices.get(parent,-1), head/scale, J.copy()) for name,(J,head,parent) in rest.items()]
        for warning in prepare(skin):
            report("warning: " + warning)
        fitted_rest = {b.name: (r,t) for b,(r,t) in zip(skin.bones,rest_local(skin.bones))}
    rest_t = {}                     # rest local translation in file units (parent joint frame)
    for name, (J, head, pname) in rest.items():
        if pname:
            rest_t[name] = rest[pname][0].T @ (head - rest[pname][1]) / scale
        else:
            rest_t[name] = head / scale

    from . import animation_report
    entries = []
    created, skipped = [], set()
    ad = arm_obj.animation_data_create()
    for an in anims:
        r_bip = 1.0
        tracks = {}
        for tr in an.tracks:
            if not tr.bone or tr.bone in tracks:
                continue
            source_scales = getattr(tr, "scales", None)
            if fit_skeleton and not is_root_translation(tr.bone, rest.get(tr.bone, (None,None,"missing"))[2]):
                tr = type(tr)(tr.bone, tr.times, tr.quats, None)       # skeleton-specific translations dropped
            tr.scales = source_scales
            tracks[tr.bone] = tr
        force_trans = set()
        if transfer_motion and "Motion" in tracks and "Bip01" in rest:
            mt = tracks.pop("Motion")
            if "Motion" in rest:
                Jm, tm0 = rest["Motion"][0], rest_t["Motion"]
            else:
                Jm = np.eye(3)
                tm0 = np.asarray(an.rest_trans.get("Motion", np.zeros(3)), float) * r_bip
            bip = tracks.get("Bip01")
            scales = getattr(bip, "scales", None)
            if trans_mode == "NONE" and bip is not None:
                times = np.union1d(bip.times, mt.times)
                rotations = P._resample(bip.times, bip.quats, times, True)
                if scales is not None:
                    scales = P._resample(bip.times, scales, times, False)
                bip = type(bip)(bip.bone, times, rotations, None)
            tracks["Bip01"] = P.transfer_motion(mt, bip, Jm, tm0, rest["Bip01"][0], rest_t["Bip01"])
            tracks["Bip01"].scales = scales
            force_trans.add("Bip01")
        if not tracks:
            continue
        action = bpy.data.actions.new(an.name)
        action["mua_source_rest_trans"] = json.dumps({k: np.asarray(v).tolist() for k,v in an.rest_trans.items()})
        action["mua_fit_skeleton"] = bool(fit_skeleton)
        action["mua_fitted_bind"] = bool(fit_skeleton)
        translation_offsets = {}
        motion_track = next((tr for tr in an.tracks if tr.bone == "Motion"), None)
        if trans_mode != "NONE" and motion_track is not None and "Motion" not in rest and not transfer_motion:
            action["mua_unbound_motion"] = json.dumps(dict(times=motion_track.times.tolist(), quats=motion_track.quats.tolist(), trans=None if motion_track.trans is None else motion_track.trans.tolist()))
        rates = [1.0/float(np.median(np.diff(tr.times))) for tr in an.tracks if len(tr.times)>1 and np.all(np.diff(tr.times)>1e-6)]
        action["mua_source_start_frame"] = scene.frame_start
        action["mua_sample_rate"] = min(120, max(1, int(round(max(rates))))) if rates else int(round(fps))
        if trans_mode != "NONE" and an.motion_velocity is not None:
            action["mua_motion_velocity"] = an.motion_velocity
            action["mua_motion_offset"] = an.motion_offset
        ad.action = action
        for name, tr in tracks.items():
            if name not in rest:
                skipped.add(name)
                continue
            J, head, pname = rest[name]
            Jp, hp = (rest[pname][0], rest[pname][1]) if pname else (None, None)
            if fit_skeleton:
                use_t = is_root_translation(name, pname) or name in force_trans
            else:
                use_t = name in force_trans or trans_mode == "ALL" or (trans_mode == "ROOT" and is_root_translation(name, pname))
            translation = tr.trans
            if fit_skeleton and not is_root_translation(name, pname) and name in fitted_rest:
                translation = np.tile(fitted_rest[name][1], (len(tr.times),1))
                use_t = True
            rot, loc = P.pose_basis_rt(tr.quats, translation, J, Jp, head, hp, scale, use_t,
                                       D.get(name), D.get(pname) if pname else None)
            frames = tr.times * fps + scene.frame_start
            pb = arm_obj.pose.bones[name]
            pb.rotation_mode = "QUATERNION"
            base = pb.path_from_id()
            for i in range(4):
                _write_curve(action, arm_obj, base + ".rotation_quaternion", i, name, frames, rot[:, i])
            scales = getattr(tr, "scales", None)
            if scales is not None:
                for i in range(3):
                    _write_curve(action, arm_obj, base + ".scale", i, name, frames, (np.asarray(scales) @ P.C)[:, i])
            if loc is not None:
                for i in range(3):
                    _write_curve(action, arm_obj, base + ".location", i, name, frames, loc[:, i])
        action["mua_translation_offsets"] = json.dumps(translation_offsets)
        existing = {(fc.data_path,fc.array_index) for fc in action.fcurves}
        for pb in arm_obj.pose.bones:
            pb.rotation_mode = "QUATERNION"
            neutral_rot, neutral_loc = (1.,0.,0.,0.), (0.,0.,0.)
            if fit_skeleton and pb.name in fitted_rest and not is_root_translation(pb.name, rest[pb.name][2]):
                from .export_core import rot_to_quat
                r,t = fitted_rest[pb.name]
                J,head,parent = rest[pb.name]
                Jp,hp = (rest[parent][0],rest[parent][1]) if parent else (None,None)
                neutral_r,neutral_t = P.pose_basis(np.array([rot_to_quat(r)]), np.array([t]), J,Jp,head,hp,scale,True)
                neutral_rot,neutral_loc = neutral_r[0],neutral_t[0]
            for channel, values in (("location", neutral_loc), ("scale", (1.,1.,1.)), ("rotation_quaternion", neutral_rot)):
                path = pb.path_from_id() + "." + channel
                for i,value in enumerate(values):
                    if (path,i) not in existing:
                        _write_curve(action, arm_obj, path, i, pb.name, np.array([scene.frame_start]), np.array([value]))
        action.use_fake_user = True
        try:
            action.use_frame_range = True
            action.frame_start = scene.frame_start
            action.frame_end = scene.frame_start + an.duration * fps
        except Exception:
            pass
        entries.append(animation_report.describe(an, action, scene.frame_start, fps, rest))
        created.append(action)
    if not created:
        raise RuntimeError("no importable animation tracks")
    animation_report.append(source_path or arm_obj.name, arm_obj.name, entries)
    ad.action = created[0]
    scene.frame_set(scene.frame_current, subframe=scene.frame_subframe)
    if skipped:
        report("note: %d track(s) had no matching bone, e.g. %s" % (len(skipped), ", ".join(sorted(skipped)[:5])))
    return created


def import_animations(path, arm_obj, trans_mode="ROOT", transfer_motion=False, fit_skeleton=False,
                      retarget=False, report=print):
    if arm_obj is None or arm_obj.type != "ARMATURE":
        raise RuntimeError("select the armature created by the MUA model importer")
    scale = float(arm_obj.get(PROP_SCALE, 0.0254))
    warns = []
    anims = load_animations(path, warns)
    for w in warns:
        report("warning: " + w)
    try:
        return _apply_anims(arm_obj, anims, trans_mode, transfer_motion, fit_skeleton, False, False, scale, report, path)
    except RuntimeError as e:
        raise RuntimeError("%s%s" % (e, (" (" + warns[-1] + ")") if warns else ""))


