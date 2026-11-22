"""Alchemy property groups (per object / per material) and the sidebar panels."""
import bpy
from bpy_extras.io_utils import ImportHelper
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty,
                       PointerProperty, StringProperty)

from . import blender_io

BLEND_ITEMS = [("0", "0: Off", "No frame-buffer blending"),
               ("1", "1: Normal", "Source alpha / one minus source alpha"),
               ("2", "2: Additive", "Source alpha / one (glows, energy effects)"),
               ("3", "3: Premultiplied", "One / one minus source alpha")]
CULL_ITEMS = [("DEFAULT", "Default", "Back-face culling like the shader default"),
              ("DISABLE", "Disable", "Draw both sides"),
              ("BACK", "Backface Culling", "Cull back faces"),
              ("FRONT", "Frontface Culling", "Cull front faces")]
ANIM_TYPE_ITEMS = [("REPEAT", "Repeat", ""), ("LOOP", "Loop", ""), ("BOUNCE", "Bounce", ""), ("CLAMP", "Clamp", "")]
SHADER_ITEMS = [("MarvelStandart", "MarvelStandart", "Marvel Ultimate Alliance standard shader")]

# igGeometry flag bits, read from TEST.igb (Max export with one flag per named mesh)
FLAG_COLLIDABLE = 0x10        # confirmed
FLAG_INVISIBLE = 0x20         # confirmed
FLAG_DYNAMIC_TRANSFORM = 0x40  # inferred (the gap between 0x20 and the confirmed 0x80)
FLAG_DYNAMIC_OBJECT = 0x80    # confirmed


def _mat_update(self, context):
    try:
        blender_io.sync_material(self.id_data)
    except Exception as e:                      # never break the UI because of a node edit
        print("MUA: material sync failed:", e)


def _alpha_mode_update(self, context):
    self.alpha_func = self.alpha_mode
    self.alpha_ref = 0.0 if self.alpha_mode == "4" else 0.5
    _mat_update(self, context)


def _texture_matrix_update(self, context):
    if not blender_io._SYNC_LOCK[0]:
        self.texture_matrix_present = True
    _mat_update(self, context)


def _color(name, default, desc=""):
    return FloatVectorProperty(name=name, subtype="COLOR", size=4, min=0.0, max=1.0, default=default,
                               description=desc, update=_mat_update)


def _img(name, desc=""):
    return PointerProperty(name=name, type=bpy.types.Image, description=desc, update=_mat_update)


def _get_opacity(self):
    return float(self.diffuse[3])


def _set_opacity(self, value):
    d = list(self.diffuse)
    d[3] = value
    self.diffuse = d


class MUAShader(bpy.types.PropertyGroup):
    active: BoolProperty(name="Alchemy material", default=False,
                         description="This material follows the MarvelStandart shader layout")
    shader_type: EnumProperty(name="Shader Type", items=SHADER_ITEMS, default="MarvelStandart")
    blend_type: EnumProperty(name="Blend", items=BLEND_ITEMS, default="0", update=_mat_update)
    alpha_test: BoolProperty(name="Alpha", description="Enable igAlphaStateAttr; new materials use function 6 and reference 0.5", default=False, update=_mat_update)
    alpha_mode: EnumProperty(name="Alpha type", items=[("6","Alpha 6","Reference 0.5"),("4","Alpha 4","Reference 0.0")], default="6", update=_alpha_mode_update)
    depth_write_present: BoolProperty(name="DepthWriteStateAttr", default=False, description="Include an explicit depth-write attribute; choose On or Off below. Unchecked omits the attribute")
    depth_write: EnumProperty(name="Depth write", items=[("1","On","Write depth"),("0","Off","Do not write depth")], default="1")
    alpha_func: EnumProperty(name="Alpha function", items=[(str(i), n, "") for i,n in enumerate(("Never", "Less", "Equal", "Less or equal", "Greater", "Not equal", "Greater or equal", "Always"))], default="6", update=_mat_update)
    alpha_ref: FloatProperty(name="Alpha threshold", default=0.5, min=0, max=1, update=_mat_update)
    texture_matrix: BoolProperty(name="TextureMatrixState", default=True, update=_texture_matrix_update,
                                 description="Enable texture-coordinate transforms; disabling preserves the UV animation for later reuse")
    texture_matrix_present: BoolProperty(default=False, options={'HIDDEN'})
    shader_parameters: BoolProperty(name="ShaderParameters", default=True, update=_mat_update,
                                   description="Export material.reflectance and material.transparent shader constants")
    lighting: BoolProperty(name="Enable Lighting", default=True, update=_mat_update)
    shininess: FloatProperty(name="Shininess", default=0.0, min=0.0, soft_max=128.0, update=_mat_update)
    diffuse: _color("Diffuse", (1.0, 1.0, 1.0, 1.0))
    ambient: _color("Ambient", (1.0, 1.0, 1.0, 1.0))
    specular: _color("Specular", (0.0, 0.0, 0.0, 0.0))
    emission: _color("Self-illumination", (0.0, 0.0, 0.0, 0.0))
    opacity: FloatProperty(name="Opacity", min=0.0, max=1.0, get=_get_opacity, set=_set_opacity,
                           description="Diffuse alpha; blending and alpha testing are configured separately")
    original_diffuse: FloatVectorProperty(size=4, default=(1.0, 1.0, 1.0, 1.0))
    diffuse_tex: _img("Diffuse Texture")
    normal_tex: _img("Normal Texture", "Tangent-space normal map (OpenGL convention); exported as DXT5nm")
    specular_tex: _img("Specular Texture")
    emissive_tex: _img("Emissive Texture")
    reflection_mode: EnumProperty(name="Mapping", items=[("CUBE", "Cube directions", "Six directional images"), ("SPHERE", "Sphere map", "One sphere-map image")], default="CUBE", update=_mat_update)
    reflection_same: BoolProperty(name="Same image for all directions", default=True, update=_mat_update)
    reflection_all: _img("Reflection Texture", "Used for all six directions")
    reflection_px: _img("+X")
    reflection_nx: _img("-X")
    reflection_py: _img("+Y")
    reflection_ny: _img("-Y")
    reflection_pz: _img("+Z")
    reflection_nz: _img("-Z")
    reflection_mask: _img("Reflection Mask Texture")
    reflectance: FloatVectorProperty(name="Reflectance", size=4, default=(1.0, 0.0, 0.0, 0.0), update=_mat_update,
                                     description="x: amount of reflection (y, z, w unused by MarvelStandart)")
    transparent: BoolProperty(name="Transparent", default=False)


class MUAObject(bpy.types.PropertyGroup):
    # Geometry
    normals: BoolProperty(name="Normals", default=True)
    texture: BoolProperty(name="Texture", default=True)
    material: BoolProperty(name="Material", default=True)
    vertex_colors: BoolProperty(name="Vertex Colors", default=False,
                                description="Export the active vertex color attribute")
    instantiation: BoolProperty(name="Instantiation", default=False)
    indexed: BoolProperty(name="Indexed geometry", default=True,
                          description="Write an index array (about 2.5x smaller) instead of a triangle soup")
    cull: EnumProperty(name="Face Culling", items=CULL_ITEMS, default="DEFAULT")
    # Flags
    collidable: BoolProperty(name="Collidable", default=True, description="Geometry flag 0x10")
    invisible: BoolProperty(name="Invisible", default=False, description="Geometry flag 0x20")
    dynamic_transform: BoolProperty(name="Dynamic Transform", default=False, description="Geometry flag 0x40 (inferred)")
    dynamic_object: BoolProperty(name="Dynamic Object", default=False, description="Geometry flag 0x80")
    segment_node: BoolProperty(name="Segment Node", default=False,
                               description="Export an igSegment node for name-based skin segment switching")
    external_object: BoolProperty(name="External Object", default=False, description="Stored only (needs an external file reference)")
    extra_flags: IntProperty(name="Extra flag bits", default=0, min=0,
                             description="Raw bits OR-ed into the node flags (for modders who know the values)")

    def node_flags(self):
        f = 0
        for flag, bit in ((self.collidable, FLAG_COLLIDABLE), (self.invisible, FLAG_INVISIBLE),
                          (self.dynamic_transform, FLAG_DYNAMIC_TRANSFORM), (self.dynamic_object, FLAG_DYNAMIC_OBJECT)):
            if flag:
                f |= bit
        return f | int(self.extra_flags)


def _is_mesh(self, obj):
    scene = self.id_data
    return obj.type == "MESH" and scene.objects.get(obj.name) == obj


def recover_texture_slots(mat):
    """Recover legacy .blend properties from real nodes, without reviving cleared slots."""
    if mat is None or not mat.mua.active or mat.node_tree is None:
        return
    pairs = (("diffuse_tex","DiffuseTexture"),("normal_tex","NormalMapTexture"),("specular_tex","SpecularTexture"),("emissive_tex","EmissiveTexture"),("reflection_mask","ReflectionMaskTexture"))
    changed = False
    with blender_io.sync_suspended():
        for prop,name in pairs:
            node = mat.node_tree.nodes.get(name)
            image = getattr(node,"image",None)
            if getattr(mat.mua,prop) is None and image is not None and not image.name.startswith("MUA_placeholder"):
                setattr(mat.mua,prop,image)
                changed = True
    if changed:
        blender_io.sync_material(mat)


def _target_changed(self, context):
    ob = self.target_object
    self.target_material = ob.active_material.name if ob and ob.active_material else ""
    if ob:
        for slot in ob.material_slots:
            recover_texture_slots(slot.material)


class MUATools(bpy.types.PropertyGroup):
    target_object: PointerProperty(name="Object", type=bpy.types.Object, poll=_is_mesh, update=_target_changed,
                                   description="Mesh edited by the Alchemy Object Property panel")
    target_material: StringProperty(name="Material", description="Material edited by the Alchemy Shader panel")


# ------------------------------------------------------------------------- panels
class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Alchemy"


def target_object(context):
    ob = context.scene.mua_tools.target_object
    if ob is not None:
        return ob
    return None


def target_material(context):
    t = context.scene.mua_tools
    ob = target_object(context)
    m = bpy.data.materials.get(t.target_material) if t.target_material else None
    if m is not None and ob is not None and any(slot.material == m for slot in ob.material_slots):
        return m
    return ob.active_material if ob is not None and ob.type == "MESH" else None


class MUA_PT_object(_Base, bpy.types.Panel):
    bl_label = "Alchemy Object Property"
    bl_idname = "MUA_PT_object"

    def draw(self, context):
        lay = self.layout
        lay.prop(context.scene.mua_tools, "target_object", text="Object")
        ob = target_object(context)
        if ob is None:
            lay.label(text="Pick a mesh object", icon="INFO")
            return
        p = ob.mua
        box = lay.box()
        box.label(text="Geometry")
        col = box.column(align=True)
        for name in ("normals", "texture", "material", "vertex_colors", "indexed"):
            col.prop(p, name)
        box.label(text="Face Culling")
        box.prop(p, "cull", expand=True)
        box = lay.box()
        box.label(text="Flags")
        col = box.column(align=True)
        for name in ("collidable", "invisible", "dynamic_transform", "dynamic_object", "segment_node"):
            col.prop(p, name)
        box.prop(p, "extra_flags")


class MUA_PT_shader(_Base, bpy.types.Panel):
    bl_label = "Alchemy Shader"
    bl_idname = "MUA_PT_shader"

    def draw(self, context):
        lay = self.layout
        lay.prop(context.scene.mua_tools, "target_object", text="Object")
        ob = target_object(context)
        if ob is not None and len(ob.material_slots) > 1:
            lay.prop_search(context.scene.mua_tools, "target_material", ob, "material_slots", text="Material")
        mat = target_material(context)
        if mat is None:
            lay.label(text="Pick a material", icon="INFO")
            return
        p = mat.mua
        if not p.active:
            lay.label(text="Not an Alchemy material yet", icon="INFO")
            lay.operator("mua.make_alchemy_material", text="Make Alchemy Material").material = mat.name
            return
        row = lay.row(align=True)
        row.prop(p, "shader_type", text="Shader Type")
        row.operator("mua.refresh_material", text="Refresh").material = mat.name
        box = lay.box()
        box.label(text="Material")
        box.prop(p, "blend_type", text="Blend 0:off/1:N/2:A/3:N")
        box.prop(p, "alpha_test")
        if p.alpha_test:
            box.prop(p, "alpha_mode", expand=True)
            box.label(text="Stored function %s; reference %.3g" % (p.alpha_func, p.alpha_ref))
        box.prop(p, "depth_write_present")
        if p.depth_write_present:
            box.prop(p, "depth_write", expand=True)
        box.prop(p, "texture_matrix")
        box.prop(p, "lighting")
        box.prop(p, "shininess")
        for name in ("diffuse", "ambient", "specular", "emission"):
            box.prop(p, name)
        box.prop(p, "opacity")
        box = lay.box()
        box.label(text="Texture Maps")
        for name in ("diffuse_tex", "normal_tex", "specular_tex", "emissive_tex"):
            image_slot(box, p, name, mat)
        box = lay.box()
        box.label(text="Environment Map")
        box.prop(p, "reflection_mode")
        if p.reflection_mode == "CUBE":
            box.prop(p, "reflection_same")
        if p.reflection_mode == "SPHERE" or p.reflection_same:
            image_slot(box, p, "reflection_all", mat)
        else:
            col = box.column(align=True)
            for name in ("reflection_px", "reflection_nx", "reflection_py", "reflection_ny", "reflection_pz", "reflection_nz"):
                image_slot(col, p, name, mat)
        image_slot(box, p, "reflection_mask", mat)
        box = lay.box()
        box.prop(p, "shader_parameters")
        col = box.column()
        col.enabled = p.shader_parameters
        col.prop(p, "reflectance", text="material.reflectance")
        col.prop(p, "transparent", text="material.transparent")


class MUA_OT_make_material(bpy.types.Operator):
    bl_idname = "mua.make_alchemy_material"
    bl_label = "Make Alchemy Material"
    bl_description = "Rebuild the shader from the assigned Alchemy textures"
    material: StringProperty()

    def execute(self, context):
        mat = bpy.data.materials.get(self.material)
        if mat is None:
            return {"CANCELLED"}
        diffuse = None                      # keep the first image of the old tree as the diffuse texture
        if mat.use_nodes and mat.node_tree:
            diffuse = next((n.image for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image
                            and not n.image.name.startswith("MUA_placeholder")), None)
        with blender_io.sync_suspended():
            mat.mua.active = True
            if diffuse is not None:
                mat.mua.diffuse_tex = diffuse
        blender_io.rebuild_nodes(mat)
        blender_io.sync_material(mat)
        return {"FINISHED"}


class MUA_OT_refresh_material(bpy.types.Operator):
    bl_idname = "mua.refresh_material"
    bl_label = "Refresh"
    bl_description = "Re-apply the Alchemy properties to the material nodes"
    material: StringProperty()

    def execute(self, context):
        blender_io.sync_material(bpy.data.materials.get(self.material))
        return {"FINISHED"}


def image_slot(layout, props, name, mat):
    row = layout.row(align=True)
    row.prop(props, name)
    op = row.operator("mua.open_texture", text="", icon="FILE_FOLDER")
    op.material, op.slot = mat.name, name


class MUA_OT_open_texture(bpy.types.Operator, ImportHelper):
    bl_idname = "mua.open_texture"
    bl_label = "Open and pack texture"
    material: StringProperty()
    slot: StringProperty()
    def execute(self, context):
        mat = bpy.data.materials.get(self.material)
        if mat is None or self.slot not in {"diffuse_tex", "normal_tex", "specular_tex", "emissive_tex", "reflection_all", "reflection_px", "reflection_nx", "reflection_py", "reflection_ny", "reflection_pz", "reflection_nz", "reflection_mask"}:
            return {"CANCELLED"}
        img = bpy.data.images.load(self.filepath, check_existing=False)
        if self.slot in {"normal_tex", "specular_tex", "reflection_mask"}:
            img.colorspace_settings.name = "Non-Color"
        img.pack()
        setattr(mat.mua, self.slot, img)
        return {"FINISHED"}


CLASSES = (MUAShader, MUAObject, MUATools, MUA_PT_object, MUA_PT_shader, MUA_OT_make_material, MUA_OT_refresh_material, MUA_OT_open_texture)


@bpy.app.handlers.persistent
def _recover_loaded_textures(_unused=None):
    for mat in bpy.data.materials:
        recover_texture_slots(mat)


def _owned_callback(callback):
    return (getattr(callback, "__module__", "") == __name__ and
            getattr(callback, "__name__", "") in {"_recover_loaded_textures", "_recover_after_enable"})


def _recover_after_enable():
    # Blender restricts bpy.data while enabling an add-on. Wait until that context ends.
    _recover_loaded_textures()
    return None


def unregister():
    for callback in list(bpy.app.handlers.load_post):
        if _owned_callback(callback):
            timer = getattr(callback,"_enable_timer",None)
            if timer is not None and bpy.app.timers.is_registered(timer):
                bpy.app.timers.unregister(timer)
            bpy.app.handlers.load_post.remove(callback)
    for callback in (_recover_after_enable, _recover_loaded_textures):
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)
    for owner,name in ((bpy.types.Scene,"mua_tools"),(bpy.types.Object,"mua"),(bpy.types.Material,"mua")):
        if hasattr(owner,name):
            delattr(owner,name)
    for c in reversed(CLASSES):
        base = bpy.types.PropertyGroup if issubclass(c,bpy.types.PropertyGroup) else (bpy.types.Panel if issubclass(c,bpy.types.Panel) else bpy.types.Operator)
        identifier = c.__name__
        if base == bpy.types.Operator:
            category,operator = c.bl_idname.split(".",1)
            identifier = category.upper()+"_OT_"+operator
        old = base.bl_rna_get_subclass_py(identifier, None)
        if old is not None and old.__module__ == __name__:
            bpy.utils.unregister_class(old)


def register():
    unregister()  # Recover from a partial enable or an in-session module reload.
    try:
        for c in CLASSES:
            bpy.utils.register_class(c)
        bpy.types.Material.mua = PointerProperty(type=MUAShader)
        bpy.types.Object.mua = PointerProperty(type=MUAObject)
        bpy.types.Scene.mua_tools = PointerProperty(type=MUATools)
        _recover_loaded_textures._enable_timer = _recover_after_enable
        bpy.app.handlers.load_post.append(_recover_loaded_textures)
        bpy.app.timers.register(_recover_after_enable, first_interval=0.0)
    except Exception:
        unregister()
        raise
