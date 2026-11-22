"""Actor-free joint animation for mannequin scene graphs."""
import numpy as np
from .playback import PLAY_MODE
from .export_core import (Builder, rest_local, split_pieces, tangent_frame, build_geometry,
                          material_attrs, uv_transform_node, helper_nodes, CULL_FRONT, CULL_BACK)


def build(path, model, swap_rb=False):
    B = Builder(); B.swap_rb = swap_rb
    identity = tuple(np.eye(4).ravel())
    anim = model.animation
    times = np.rint(np.arange(anim.n) / anim.rate * 1e9).astype(np.int64)
    locals_ = rest_local(model.bones)
    matrices, joints = [], []
    for i, bone in enumerate(model.bones):
        matrix = B.obj('igMatrixObject', {2:identity}); matrices.append(matrix)
        q,t = anim.tracks[bone.name]
        f = {3:B.numlist('igQuaternionfList',q,'<f4'),
             8:PLAY_MODE[anim.playback_type],
             11:B.numlist('igLongList',times,'<i8'),15:2,16:bytes((1,3,1)),18:int(times[-1])}
        if t is not None:
            f[2]=B.numlist('igVec3fList',t,'<f4'); f[15] |= 1
        if bone.name in anim.scales:
            f[4]=B.numlist('igVec3fList',anim.scales[bone.name],'<f4'); f[15] |= 8
        local=np.eye(4);local[:3,:3],local[:3,3]=locals_[i]
        world=np.eye(4);world[:3,:3],world[:3,3]=bone.frame,bone.head
        joints.append(B.obj('igJoint',{2:bone.name,5:2,8:tuple(local.T.ravel()),9:0,
                    10:B.obj('igTransformSequence1_5',f),11:tuple(np.linalg.inv(world).T.ravel()),12:matrix}))
    for i,joint in enumerate(joints):
        joint.fields[7]=B.objlist('igNodeList',[joints[k] for k,b in enumerate(model.bones) if b.parent==i])
    nodes=[joints[i] for i,b in enumerate(model.bones) if b.parent<0]
    images,cache=[],{}
    for mesh in model.meshes:
        attrs=material_attrs(B,mesh.mat,cache,images) if getattr(mesh,'use_material',True) else []
        vbs=B.obj('igVertexBlendStateAttr');B.set_kind(vbs,'Bool',1);attrs.append(vbs)
        if mesh.cull!='DEFAULT':
            cull=B.obj('igCullFaceAttr');B.set_kind(cull,'Bool',mesh.cull!='DISABLE')
            B.set_kind(cull,'Enum',CULL_FRONT if mesh.cull=='FRONT' else CULL_BACK);attrs.append(cull)
        pos=mesh.positions;T,Bn=tangent_frame(pos,mesh.normals,mesh.uv,mesh.faces)
        pieces,bmj,weights=split_pieces(mesh,{i:i for i in range(len(joints))})
        geos=[]
        for pal,tris in pieces:
            f=mesh.faces[tris].reshape(-1);slots={b:s for s,b in enumerate(pal)}
            ji=np.array([[slots.get(int(b),0) for b in row] for row in bmj[f]],np.uint8)
            geo=build_geometry(B,mesh.name,pos[f],mesh.normals[f],mesh.uv[f],Bn[f],T[f],len(tris),mesh.flags,
                weights[f].astype(np.float32),ji,getattr(mesh,'indexed',False),getattr(mesh,'use_normals',True),
                getattr(mesh,'use_uv',True),mesh.colors[f] if getattr(mesh,'colors',None) is not None else None)
            palette=B.obj('igVertexBlendMatrixListAttr',{4:B.objlist('igObjectList',[matrices[i] for i in pal]),5:identity,6:identity})
            geos.append(B.obj('igAttrSet',{2:mesh.name,7:B.objlist('igNodeList',[geo]),8:B.objlist('igAttrList',[palette])}))
        child=B.obj('igAttrSet',{2:mesh.name,7:B.objlist('igNodeList',geos),8:B.objlist('igAttrList',[])})
        if mesh.mat.uv_anim is not None:child=uv_transform_node(B,mesh.mat,child)
        nodes.append(B.obj('igAttrSet',{2:mesh.mat.name,7:B.objlist('igNodeList',[child]),8:B.objlist('igAttrList',attrs)}))
    allp=np.concatenate([m.positions for m in model.meshes])
    bound=B.obj('igAABox',{2:tuple(allp.min(0)),3:tuple(allp.max(0))})
    root=B.obj('igAttrSet',{2:model.name,3:bound,7:B.objlist('igNodeList',nodes+helper_nodes(B,model)),8:B.objlist('igAttrList',[])})
    textures=[v[1] for v in cache.values() if v[1].meta.name=='igTextureAttr']
    scene=B.obj('igSceneInfo',{2:'Scene Graph',4:1,5:root,6:B.objlist('igTextureList',textures),
        7:B.objlist('igGraphPathList',[]),8:0,9:int(times[-1]),10:(0.,0.,1.),11:B.objlist('igNodeList',[])})
    infos=[scene]
    if getattr(model,'generate_hull',False):
        from .collision import build as hull
        infos.append(hull(B,model.meshes))
    B.finish(path,B.objlist('igInfoList',infos))
    return dict(joints=len(joints),meshes=len(model.meshes),samples=anim.n)
