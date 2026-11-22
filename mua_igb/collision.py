"""Raven collision lists, matching the supplied converter's no-sort BVH layout."""
import numpy as np

def build(B, meshes):
    pieces = [m.positions[m.faces] for m in meshes if m.flags & 0x10 and len(m.faces)]
    if not pieces:
        raise ValueError("No collidable triangles for collision hull")
    tri = np.concatenate(pieces).astype('<f4')
    n = len(tri)
    leaves = 1
    while leaves * 16 < n:
        leaves *= 2
    nodes = np.zeros((2*leaves, 8), '<f4')
    tags = nodes.view('<u4')
    tags[:-1, 3] = 507
    tags[:-1, 7] = 507
    def bounds(i, a, b):
        pts = tri[a:b].reshape(-1, 3)
        nodes[i, :3], nodes[i, 4:7] = pts.min(0), pts.max(0)
        if i < leaves-1:
            mid = a + (b-a+1)//2
            bounds(2*i+1, a, mid)
            bounds(2*i+2, mid, b)
    bounds(0, 0, n)
    nodes[-1] = nodes[0]
    tags[-1, 3], tags[-1, 7] = 0x7C36C81E, 0x10013D76
    packed = np.zeros((n, 3, 4), '<f4')
    packed[:, :, :3] = tri
    packed.view('<u4')[:, 1, 3] = 507
    return B.obj("igCollideHull", {2: "igCollideHull0", 4: 1,
        5: B.numlist("igFloatList", packed.reshape(-1), '<f4'),
        6: B.numlist("igFloatList", nodes.reshape(-1), '<f4'), 7: n, 8: len(nodes)-1})
