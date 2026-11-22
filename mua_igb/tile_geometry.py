"""Decode the self-contained geometry assets in Spider-Man 3 Wii tile libraries.

Placement/procedural road definitions are retained in the source file; this
imports the geometry library, not a generated city or collision proxy meshes.
"""
import struct
import numpy as np


def load_models(g, swap=False):
    from .mua_model import Model, MeshData, State, _strip_to_tris, _finish_mesh
    models=[]
    for ob in g.by_type('asTileGeometry'):
        count=int(ob.get(11,0))
        if count<=0:
            continue
        vertex=g.ref(ob.get(10,-1));lengths=g.ref(ob.get(12,-1))
        if vertex is None or lengths is None:
            continue
        stride=len(vertex.data)//count
        if stride not in (12,16) or len(vertex.data)!=count*stride:
            raise ValueError('Unsupported tile vertex size: %s' % stride)
        values=np.frombuffer(vertex.data,dtype='<u2').reshape(count,stride//2)
        lo,hi=np.asarray(ob.get(15),float),np.asarray(ob.get(16),float)
        uvlo,uvhi=np.asarray(ob.get(17),float),np.asarray(ob.get(18),float)
        mesh=MeshData()
        mesh.positions=(lo+(hi-lo)*values[:,:3]/65535).astype(np.float32)
        mesh.uv=(uvlo+(uvhi-uvlo)*values[:,4:6]/65535).astype(np.float32)
        # Packed normals are a separate game-specific encoding; Blender derives
        # normals from the decoded triangles rather than reading them as floats.
        mesh.normals=None
        mesh.weights=mesh.joints=None
        mesh.has_colors=stride==16
        mesh.colors=(np.frombuffer(vertex.data,dtype='u1').reshape(count,stride)[:,12:16].astype(np.float32)/255 if stride==16 else None)
        faces=[];offset=0
        strips=np.frombuffer(lengths.data,dtype='<u2')[:int(ob.get(13,0))]
        if int(strips.sum())!=count:
            raise ValueError('Tile triangle-strip lengths do not match the vertex count')
        for length in strips:
            faces.extend(_strip_to_tris(np.arange(offset,offset+int(length))))
            offset+=int(length)
        mesh.faces=np.asarray(faces,dtype=np.int64).reshape(-1,3)
        mesh.indexed=False
        model=Model();model.version=g.version;model.dxt_swapped=swap
        model.name='tile_%08X' % ob.get(2)
        model.source_skeleton=None
        state=State({},swap)
        state.params['external_texture_id']='%08X' % struct.unpack('<I',ob.get(7))[0]
        model.meshes=[_finish_mesh(mesh,model.name,state,[],None,None,{}, {},model.warnings)]
        models.append(model)
    return models
