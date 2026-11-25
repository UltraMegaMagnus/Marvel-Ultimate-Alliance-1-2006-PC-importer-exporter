bl_info = {
    "name": "MUA1 IGB Importer / Exporter (model + animation)",
    "author": "generated for MUA1 modding",
    "version": (0, 7, 28),
    "blender": (4, 2, 0),
    "location": "File > Import/Export > MUA1 ...; sidebar (N) > Alchemy",
    "description": "Alchemy model and animation tools for MUA1 PC, with XML/XML2, PSP, Wii, prototype and additional Alchemy game import",
    "category": "Import-Export",
}

try:
    import bpy
except ImportError:          # allows the pure-python modules to be tested outside Blender
    bpy = None

if bpy is not None:
    from bpy.props import StringProperty, FloatProperty, BoolProperty, EnumProperty, IntProperty
    from bpy_extras.io_utils import ImportHelper, ExportHelper
    # Blender may reload only this package during an in-session ZIP upgrade.
    # Reload retained submodules so registration cannot call the previous release's code.
    import importlib
    import sys
    for _name, _module in list(sys.modules.items()):
        if _name.startswith(__name__ + ".") and getattr(_module, "__file__", "").endswith(".py"):
            importlib.reload(_module)
    from . import blender_io, blender_export, props

    def _report(op, msgs):
        for m in msgs:
            op.report({"WARNING" if m.startswith(("warning", "note")) else "INFO"}, m)

    # ------------------------------------------------------------------ import
    class IMPORT_OT_mua_model(bpy.types.Operator, ImportHelper):
        bl_idname = "import_scene.mua_igb_model"
        bl_label = "Import MUA1 Model (.igb)"
        bl_options = {"REGISTER", "UNDO"}
        filename_ext = ".igb"
        filter_glob: StringProperty(default="*.igb", options={"HIDDEN"})
        scale: FloatProperty(name="Scale", default=0.0254, min=1e-6, precision=4,
                             description="Units to metres (MUA units are ~inches: 0.0254)")
        flip_green: BoolProperty(name="Flip normal Y (DirectX)", default=False, options={"HIDDEN"},
                                 description="Invert the green channel of the normal map; turn off if lighting looks inverted")
        swap_rb: BoolProperty(name="Swap color", default=False,
                              description="Swap red and blue on import, except normal maps. Independent of the export setting")
        save_textures: BoolProperty(name="Save textures as PNG", default=False, options={"HIDDEN"},
                                    description="Write the decoded textures to <model>_textures/ next to the .igb and load them from there. Off: pack them into the .blend")
        merge_names: BoolProperty(name="Merge meshes with same name", default=True)
        import_anim: BoolProperty(name="Import embedded animation", default=True,
                                  description="Files with animated joints/nodes (e.g. animated objects) also get their animation as an Action")

        def execute(self, context):
            msgs = []
            try:
                arm, objs = blender_io.import_model(self.filepath, self.scale, self.flip_green, "SWAP" if self.swap_rb else "NONE",
                                                    False, self.import_anim, msgs.append, merge_names=self.merge_names)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.report({"ERROR"}, "IGB import failed: %s" % e)
                return {"CANCELLED"}
            _report(self, msgs)
            self.report({"INFO"}, "Imported %d mesh(es)%s" % (len(objs), " + armature" if arm else ""))
            return {"FINISHED"}

    class IMPORT_OT_mua_anim(bpy.types.Operator, ImportHelper):
        bl_idname = "import_scene.mua_igb_anim"
        bl_label = "Import MUA1 Animation (.igb)"
        bl_options = {"REGISTER", "UNDO"}
        filename_ext = ".igb"
        filter_glob: StringProperty(default="*.igb", options={"HIDDEN"})
        skeleton_support: EnumProperty(name="Skeleton support", default="VANILLA", items=[("ALL", "Support for all skeletons", "Remove all translations, useful for posing custom rig"), ("VANILLA", "Support for Vanilla skeleton", "Keep source translations")])
        trans_mode: EnumProperty(
            name="Bone translation", default="ROOT", options={"HIDDEN"},
            items=[("ROOT", "Root only", "Bip01 / Motion / Footsteps: keeps bone lengths of the target model"),
                   ("ALL", "All bones", "Use every translation stored in the animation"),
                   ("NONE", "None", "Rotation only")])
        transfer_motion: BoolProperty(
            name="Transfer Motion to Bip01", default=False,
            description="Add the Motion bone's (root motion) movement onto Bip01 so the character itself travels; the Motion bone is left at rest")
        fit_skeleton: BoolProperty(
            name="Fit to skeleton (ignore skeleton-specific translations)", default=False, options={"HIDDEN"},
            description="Rotation only, plus the Motion root-motion bone: no translation made for a specific "
                        "skeleton length is applied, so the animation works on any skeleton. Off: translations "
                        "follow 'Bone translation' and are scaled to the target skeleton's bone lengths")

        def draw(self, context):
            self.layout.prop(self, "skeleton_support")
            row = self.layout.row()
            row.prop(self, "transfer_motion")

        def execute(self, context):
            arm = context.object if context.object and context.object.type == "ARMATURE" else None
            if arm is None:
                arm = next((o for o in context.selected_objects if o.type == "ARMATURE"), None)
            msgs = []
            try:
                acts = blender_io.import_animations(self.filepath, arm, "ALL" if self.skeleton_support == "VANILLA" else "NONE", self.transfer_motion,
                                                    False, False, msgs.append)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.report({"ERROR"}, "IGB animation import failed: %s" % e)
                return {"CANCELLED"}
            _report(self, msgs)
            self.report({"INFO"}, "Created %d action(s) (fake-user protected; first one assigned)" % len(acts))
            return {"FINISHED"}

    # ------------------------------------------------------------------ export
    class _AnimExportOptions:
        sample_rate: IntProperty(name="Sample rate", default=0, min=0, max=240, description="Samples per second; 0 uses the scene frame rate for models")
        """The 3ds Max 'Animation' rollout options (shared by model and animation export)."""
        export_animation: BoolProperty(
            name="Export Animation", default=True,
            description="Include embedded scene-node animation and animated texture matrices")
        force_sample: BoolProperty(name="Force sample", default=False,
                                   description="Sample at a fixed number of intervals instead of the existing keys")
        intervals: IntProperty(name="# Intervals", default=1, min=1, max=2000,
                               description="Number of sampling intervals when Force sample is on")
        anim_type: EnumProperty(name="Animation Type", items=props.ANIM_TYPE_ITEMS, default="REPEAT",
                                description="Playback mode for exported animation and animated textures")
        static_frame: IntProperty(name="Static Frame #", default=0, min=0,
                                  description="Frame at which the model is evaluated when Export Animation is on")

    class EXPORT_OT_mua_model(bpy.types.Operator, ExportHelper, _AnimExportOptions):
        bl_idname = "export_scene.mua_igb_model"
        bl_label = "Export MUA1 Model (.igb)"
        bl_options = {"REGISTER"}
        filename_ext = ".igb"
        filter_glob: StringProperty(default="*.igb", options={"HIDDEN"})
        mode: EnumProperty(name="Type", default="SKIN", items=[("SKIN", "Actor", "Character with a skeleton"), ("STATIC", "Model", "Export model geometry"), ("MANNEQUIN", "Mannequin", "Actor-free animated mannequin")])
        export_animation: BoolProperty(name="Export Animation",
            get=lambda self: self.get("animation_" + self.mode, self.mode != "SKIN"),
            set=lambda self, value: self.__setitem__("animation_" + self.mode, bool(value)),
            description="Embed the active armature action. Actor material UV animation is exported independently")
        generate_mipmaps: BoolProperty(name="Generate mipmaps", default=False)
        generate_hull: BoolProperty(name="Generate igCollideHull", default=False)
        selected_only: BoolProperty(name="Selected meshes only", default=False, options={"HIDDEN"},
                                    description="If meshes are selected export only those, otherwise every visible mesh")
        auto_scale: BoolProperty(name="Use the armature's import scale", default=True)
        scale: FloatProperty(name="Scale", default=0.0254, min=1e-6, precision=4,
                             description="Metres per MUA unit (used when the armature has no stored import scale)")
        normalize_frames: BoolProperty(
            name="Standard bone axes", default=False,
            description="Write bones whose axes differ from the game's by a multiple of 90 degrees with the game's axes, "
                        "so animations deform the skin correctly (mostly useful for non-vanilla skeletons)")
        flip_green: BoolProperty(name="Flip normal Y (DirectX)", default=False, options={"HIDDEN"})
        swap_rb: BoolProperty(name="Swap color", default=False,
                              description="Off: textures are written exactly as they are in Blender. On: red and blue are "
                                          "exchanged on every texture except normal maps, regardless of the import setting")
        anim_workaround: BoolProperty(name="Animation workaround", default=True, description="Bake skeletal animation to actor-free joint/mesh deformation. Off exports a static pose at Static frame; UV animation remains independent")

        def draw(self, context):
            lay = self.layout
            box = lay.box()
            box.prop(self, "mode")
            box = lay.box()
            box.prop(self, "swap_rb")
            box.prop(self, "generate_mipmaps")
            if self.mode in ("STATIC","MANNEQUIN"):
                box.prop(self, "generate_hull")
            if self.mode == "MANNEQUIN":
                box.prop(self,"anim_workaround")
            box = lay.box()
            box.label(text="Animation")
            box.prop(self, "export_animation")
            sub = box.column()
            sub.enabled = self.export_animation
            for n in ("sample_rate", "force_sample", "intervals", "anim_type", "static_frame"):
                sub.prop(self, n)

        def execute(self, context):
            try:
                kind, info, warns = blender_export.export_model(context, self.filepath, self)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.report({"ERROR"}, "IGB export failed: %s" % e)
                return {"CANCELLED"}
            for w in warns:
                self.report({"WARNING"}, w)
            self.report({"INFO"}, "Exported %s: %s" % (kind, info))
            return {"FINISHED"}

    class EXPORT_OT_mua_anim(bpy.types.Operator, ExportHelper, _AnimExportOptions):
        bl_idname = "export_scene.mua_igb_anim"
        bl_label = "Export MUA1 Animation (.igb)"
        bl_options = {"REGISTER"}
        filename_ext = ".igb"
        filter_glob: StringProperty(default="*.igb", options={"HIDDEN"})
        auto_scale: BoolProperty(name="Use the armature's import scale", default=True)
        scale: FloatProperty(name="Scale", default=0.0254, min=1e-6, precision=4)
        normalize_frames: BoolProperty(
            name="Standard bone axes", default=False,
            description="Convert rotations of bones with non-standard axes back to the game's axes")
        extract_motion: BoolProperty(name="Extract Motion", default=False, description="Move unextracted horizontal Bip01 travel to Motion and write Raven motion velocity")
        post_compress: BoolProperty(name="Compressed (Enbaya) Post Export", default=True, description="Compress after RemovePos. Motion and tracks requiring scale keys keep their raw keys")
        remove_pos: BoolProperty(name="RemovePos", default=True)
        track_format: EnumProperty(
            name="Track format", default="SEQUENCE",
            items=[("SEQUENCE", "Raw keyframes (for the Alchemy optimizers)",
                    "Every track is an igTransformSequence1_5 keyed on each sample, first frame included. Run Extract Motion / compression afterwards"),
                   ("ENBAYA", "Compressed (Enbaya)", "Game-ready like the game's own files; Extract Motion cannot process it")])
        sample_rate: IntProperty(name="Sample rate", default=0, min=0, max=120,
                                 description="Samples per second (0 = imported sample rate, otherwise scene frame rate)")

        def draw(self, context):
            lay = self.layout
            box = lay.box()
            box.prop(self, "track_format")
            box.prop(self, "remove_pos")
            row = box.row()
            row.enabled = self.remove_pos and self.track_format == "SEQUENCE"
            row.prop(self, "post_compress")
            box.prop(self, "extract_motion")
            box.prop(self, "sample_rate")
            box = lay.box()
            box.label(text="Sampling")
            box.prop(self, "force_sample")
            sub = box.column()
            sub.enabled = self.force_sample
            sub.prop(self, "intervals")
            box.prop(self, "anim_type")

        def execute(self, context):
            try:
                info, name = blender_export.export_action(context, self.filepath, self)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.report({"ERROR"}, "IGB animation export failed: %s" % e)
                return {"CANCELLED"}
            self.report({"INFO"}, "Exported action '%s': %s" % (name, info))
            return {"FINISHED"}

    def _menu_import(self, context):
        self.layout.operator(IMPORT_OT_mua_model.bl_idname, text="MUA1 Model (.igb)")
        self.layout.operator(IMPORT_OT_mua_anim.bl_idname, text="MUA1 Animation (.igb)")

    def _menu_export(self, context):
        self.layout.operator(EXPORT_OT_mua_model.bl_idname, text="MUA1 Model (.igb)")
        self.layout.operator(EXPORT_OT_mua_anim.bl_idname, text="MUA1 Animation (.igb)")

    _classes = (IMPORT_OT_mua_model, IMPORT_OT_mua_anim, EXPORT_OT_mua_model, EXPORT_OT_mua_anim)

    def unregister():
        for menu,names in ((bpy.types.TOPBAR_MT_file_import,{"_menu_import"}),
                           (bpy.types.TOPBAR_MT_file_export,{"_menu_export"})):
            callbacks = list(getattr(menu.draw,"_draw_funcs",[]))
            for callback in callbacks:
                if getattr(callback,"__module__",None)==__name__ and callback.__name__ in names:
                    menu.remove(callback)
        for c in reversed(_classes):
            category,operator = c.bl_idname.split(".",1)
            old = bpy.types.Operator.bl_rna_get_subclass_py(category.upper()+"_OT_"+operator,None)
            if old is not None and old.__module__ == __name__:
                bpy.utils.unregister_class(old)
        props.unregister()

    def register():
        unregister()
        try:
            props.register()
            for c in _classes:
                bpy.utils.register_class(c)
            bpy.types.TOPBAR_MT_file_import.append(_menu_import)
            bpy.types.TOPBAR_MT_file_export.append(_menu_export)
        except Exception:
            unregister()
            raise
