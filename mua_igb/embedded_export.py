"""Export rigid animated scene nodes as live igTransform sequences."""
import numpy as np
from .playback import PLAY_MODE
from . import pose_math as P
from .export_core import XStatic, XSkin, XAnim


def collect(context, arm, objects, collector, scale, options, name):
    from mathutils import Matrix
    from .blender_export import collect_bones, collect_object
    infos, index = collect_bones(arm, scale, False)
    model = XStatic(name)
    for ob in objects:
        model.meshes.extend(collect_object(ob, collector, scale, index, 0))
    # Blended weights need the actor/skin graph, not independent rigid transforms.
    blended = any(np.any((m.weights > 1e-6).sum(axis=1) != 1) or np.any(m.joints[np.arange(len(m.joints)),np.argmax(m.weights,axis=1)][m.faces] != m.joints[np.arange(len(m.joints)),np.argmax(m.weights,axis=1)][m.faces][:,:1]) for m in model.meshes)
    if blended:
        from .blender_export import sample_pose, xbones
        action = arm.animation_data.action if arm.animation_data else None
        if action is None:
            raise ValueError('The embedded animated rig has no active action')
        fps = context.scene.render.fps / context.scene.render.fps_base
        rate,n,pbs,rot,loc,scales = sample_pose(context,arm,action,options.sample_rate or fps,options.intervals if options.force_sample else 0)
        skin = XSkin(name)
        skin.meshes,skin.bones = model.meshes,xbones(infos,scale)
        anim = XAnim(action.name,rate,n)
        anim.playback_type = options.anim_type
        by_name = {p.name:i for i,p in enumerate(pbs)}
        for info in infos:
            j = by_name[info.name]
            parent = infos[info.parent] if info.parent >= 0 else None
            anim.tracks[info.name] = P.anim_from_basis(rot[:,j],loc[:,j],info.J,parent.J if parent else None,info.head,parent.head if parent else None,scale)
            anim.scales[info.name] = scales[:,j] @ P.C.T
        skin.animation = anim
        return skin
    import copy
    used, pieces = set(), []
    for mesh in model.meshes:
        active = mesh.weights > 1e-6
        if np.any(active.sum(axis=1) != 1):
            raise ValueError('Animated scene-node export requires rigid vertex weights; blended mesh: ' + mesh.name)
        rigid = mesh.joints[np.arange(len(mesh.joints)), np.argmax(mesh.weights,axis=1)]
        face_bones = rigid[mesh.faces]
        if np.any(face_bones != face_bones[:,:1]):
            raise ValueError('Animated scene-node mesh has triangles spanning different bones: ' + mesh.name)
        for i in np.unique(face_bones):
            piece = copy.copy(mesh)
            piece.faces = mesh.faces[face_bones[:,0] == i].copy()
            piece.animation_bone = infos[int(i)].name
            used.add(piece.animation_bone)
            pieces.append(piece)
    model.meshes = pieces
    scene = context.scene
    fps = scene.render.fps / scene.render.fps_base
    start, end = scene.frame_start, scene.frame_end
    count = int(options.intervals) if options.force_sample else int(np.ceil((end-start)/fps*(options.sample_rate or fps)))
    frames = np.linspace(start,end,count+1) if end > start else np.array([float(start)])
    times = (frames-start)/fps
    samples = {name: [] for name in used}
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    try:
        for frame in frames:
            scene.frame_set(int(np.floor(frame)), subframe=float(frame-np.floor(frame)))
            ev = arm.evaluated_get(context.evaluated_depsgraph_get())
            for name in used:
                matrix = ev.matrix_world @ ev.pose.bones[name].matrix @ arm.data.bones[name].matrix_local.inverted() @ arm.matrix_world.inverted()
                t, q, sc = matrix.decompose()
                reconstructed = Matrix.LocRotScale(t,q,sc)
                if not np.allclose(matrix, reconstructed, atol=1e-5):
                    raise ValueError('Animated node contains shear that cannot be represented: ' + name)
                samples[name].append((np.array(q.to_matrix()), np.array(t)/scale, np.array(sc)))
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
    for mesh in model.meshes:
        values = samples[mesh.animation_bone]
        rotations = np.array([v[0] for v in values])
        q = P.mats_to_quat_wxyz(rotations.transpose(0,2,1))
        mesh.node_animation = dict(times=times, quats=q[:,[1,2,3,0]], trans=np.array([v[1] for v in values]), scales=np.array([v[2] for v in values]), mode=options.anim_type)
    return model


def wrap(builder, mesh, child):
    data = getattr(mesh, 'node_animation', None)
    if data is None:
        return child
    B = builder
    times = np.rint(data['times']*1e9).astype(np.int64)
    seq = B.obj('igTransformSequence1_5', {
        2:B.numlist('igVec3fList',data['trans'],'<f4'),
        3:B.numlist('igQuaternionfList',data['quats'],'<f4'),
        4:B.numlist('igVec3fList',data['scales'],'<f4'),
        8:PLAY_MODE[data['mode']],
        11:B.numlist('igLongList',times,'<i8'),15:11,16:bytes((1,3,1)),18:int(times[-1])})
    from .export_core import subtree_bound
    return B.obj('igTransform',{2:mesh.animation_bone,3:subtree_bound(B,child),5:2,7:B.objlist('igNodeList',[child]),8:tuple(np.eye(4).ravel()),9:0,11:seq})
