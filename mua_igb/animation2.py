"""Alchemy Animation2 clips with separate rotation/translation quantization."""
import struct
import numpy as np
from .mua_model import R
from . import enbaya


def load(g, warnings):
    from .mua_anim import Anim, Track, _clip_enbaya
    result = []
    skeletons = [R(g,c,3) for c in g.by_type('igAnimationCombiner2')]
    sk = next((s for s in skeletons if s is not None), None)
    rest = {b.get(2):np.asarray(b.get(5,(0,0,0)),float)
            for b in g.obj_list(R(g,sk,3))} if sk else {}
    for ob in g.by_type('igAnimation2RotSlpTxLrpClpEn'):
        memory = g.ref(ob.get(6,-1))
        names = R(g,ob,8)
        names_mem = g.ref(names.get(4,-1)) if names else None
        if memory is None or names_mem is None:
            warnings.append('%s: external Animation2 data is unavailable' % ob.get(2))
            continue
        blob = memory.data
        if len(blob) < 84:
            raise ValueError('Truncated Animation2 compression header')
        qerr, terr = struct.unpack_from('<ff',blob,8)
        if qerr <= 0 or terr <= 0:
            raise ValueError('Invalid Animation2 quantization')
        # This stream adds translation error after rotation error. The delta
        # coding and channel state machine remain those of the Enbaya format.
        rate,duration,tracks = enbaya.decode(blob[:12]+blob[16:])
        indices = np.frombuffer(names_mem.data,dtype='<i4')[:names.get(2,0)]
        if len(indices)!=len(tracks) or any(i<0 or i>=len(g.names) for i in indices):
            raise ValueError('Animation2 track-name count mismatch')
        a = Anim(ob.get(2) or 'animation',duration)
        a.rest_trans = rest.copy()
        mask = R(g,ob,7)
        mem = g.ref(mask.get(4,-1)) if mask else None
        words = np.frombuffer(mem.data,dtype='<u4') if mem else None
        for i,(name_index,samples) in enumerate(zip(indices,tracks)):
            times,q,t = _clip_enbaya(samples,rate,duration)
            active = words is None or (i//32<len(words) and int(words[i//32]) & (1<<(i%32)))
            t = t*(terr/qerr) if active else None
            a.tracks.append(Track(g.names[int(name_index)],times,q,t))
        result.append(a)
    return result
