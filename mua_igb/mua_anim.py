"""Extract animations from an MUA1 .igb (igAnimationDatabase).

Supports Enbaya-compressed tracks (igEnbayaTransformSource) and plain keyframe
tracks (igTransformSequence1_5), QS and Xbox prototype Raven quantized tracks.
"""
import struct

import numpy as np

from .igb import IGB, Obj
from . import enbaya
from .mua_model import R, ref_of


class Track:
    def __init__(self, bone, times, quats, trans, scales=None):
        self.scales = scales
        self.bone = bone
        self.times = times      # float64 seconds, shape (n,)
        self.quats = quats      # (n,4) x,y,z,w  (Alchemy convention, see blender_io)
        self.trans = trans      # (n,3) or None -> keep the bone's rest translation


class Anim:
    def __init__(self, name, duration):
        self.name, self.duration, self.tracks = name, duration, []
        self.motion_velocity, self.motion_offset = None, (0,0,0)
        self.rest_trans = {}    # bone name -> local rest translation of the skeleton the anim was made for


def _nlerp(a, b, f):
    if np.dot(a, b) < 0:
        b = -b
    q = a * (1 - f) + b * f
    return q / (np.linalg.norm(q) or 1.0)


def _clip_enbaya(samples, rate, duration):
    """samples: list of 7-tuples. Returns times, quats, trans with the last
    sample re-evaluated exactly at `duration` when the grid overshoots it."""
    n = len(samples)
    arr = np.array(samples, np.float64)
    times = np.arange(n) / float(rate)
    if n >= 2 and times[-1] > duration + 1e-4:
        f = (duration - times[-2]) / (times[-1] - times[-2])
        f = min(max(f, 0.0), 1.0)
        last = np.concatenate([_nlerp(arr[-2, :4], arr[-1, :4], f),
                               arr[-2, 4:] * (1 - f) + arr[-1, 4:] * f])
        arr[-1] = last
        times[-1] = duration
    return times, arr[:, :4], arr[:, 4:]


def _skeleton_rest(g, anim, cache):
    """Rest translations (by bone name) of the skeleton this animation was authored on.
    Files carry a per-animation 'defaultSkeleton' plus the real '<name>_skel'; prefer the latter."""
    best = None
    for b in g.obj_list(R(g, anim, 4)):
        sk = R(g, b, 2)
        if sk is None:
            continue
        if best is None or (best.get(2) == "defaultSkeleton" and sk.get(2) != "defaultSkeleton"):
            best = sk
    if best is None:
        candidates = g.by_type("igSkeleton")
        if len(candidates) == 1:
            best = candidates[0]
        else:
            return {}
    if best.index not in cache:
        out = {}
        infos = g.obj_list(R(g, best, 4))
        tm = g.ref(best.get(3, -1))
        if tm is not None and getattr(tm, "data", b"") and len(tm.data) >= 12 * len(infos):
            for i, inf in enumerate(infos):
                out[inf.get(2)] = np.array(struct.unpack_from("<3f", tm.data, i * 12), np.float64)
        cache[best.index] = out
    return cache[best.index]


def load_animations(path, warnings=None, include_static=False):
    warnings = warnings if warnings is not None else []
    g = IGB(path)
    cache = {}
    skcache = {}
    anims = []
    for an in g.by_type("igAnimation"):
        dur = (an.get(9, 0) or 0) / 1e9
        A = Anim(an.get(2) or "anim%d" % an.index, dur)
        A.rest_trans = _skeleton_rest(g, an, skcache)
        A.skeleton_names = {sk.get(2) for binding in g.obj_list(R(g,an,4))
                            for sk in [R(g,binding,2)] if sk is not None}
        A.reference_rest = {}
        for binding in g.obj_list(R(g,an,4)):
            sk = R(g,binding,2)
            if sk is None or sk.get(2)!="defaultSkeleton":
                continue
            infos = g.obj_list(R(g,sk,4))
            mem = g.ref(sk.get(3,-1))
            if mem is not None and len(mem.data)>=12*len(infos):
                A.reference_rest = {b.get(2):np.asarray(struct.unpack_from("<3f",mem.data,i*12))
                                    for i,b in enumerate(infos)}
            break
        for tr in g.obj_list(R(g, an, 5)):
            if tr is None:
                continue
            bone = tr.get(2)
            if bone == "Motion" and tr.get(4, (0,0,0,0))[3] == -999:
                A.motion_velocity = tr.get(4)
                A.motion_offset = tr.get(5, (0,0,0))
            src = R(g, tr, 3)
            if src is None:
                # Constant tracks are actual channels, not absent animation.
                q = np.asarray(tr.get(4, (0,0,0,0)), float)
                if (dur > 0 or include_static) and np.linalg.norm(q) > 1e-6:
                    t = np.asarray(tr.get(5, (0,0,0)), float)
                    # No source plus an absent translation mask and zero fallback
                    # denotes a rest-offset channel, not a keyed origin (1201).
                    # Real curves and explicitly enabled zero channels stay live.
                    A.tracks.append(Track(bone, np.array([0.,dur]), np.tile(q/np.linalg.norm(q),(2,1)), (np.tile(t,(2,1)) if R(g, an, 10) is not None or np.any(t) else None)))
                continue
            if src.type == "igEnbayaTransformSource":
                asrc = R(g, src, 3)
                if asrc is None:
                    continue
                if asrc.index not in cache:
                    try:
                        cache[asrc.index] = enbaya.decode(g.ref(asrc.first("MemoryRef")).data)
                    except Exception as e:
                        cache[asrc.index] = None
                        warnings.append("%s: enbaya decode failed (%s)" % (A.name, e))
                dec = cache[asrc.index]
                if dec is None:
                    continue
                rate, sdur, tracks = dec
                ti = src.get(2, 0)
                if ti >= len(tracks):
                    continue
                times, q, t = _clip_enbaya(tracks[ti], rate, sdur)
                if not np.any(t):           # no translation channel in the stream
                    t = None
                A.tracks.append(Track(bone, times, q, t))
                if dur == 0:
                    A.duration = max(A.duration, float(times[-1]))
                A.duration = max(A.duration, float(times[-1])) if dur == 0 else A.duration
            elif src.type.startswith("igTransformSequence"):
                tl = ref_of(g, src, "igLongList")
                ql = ref_of(g, src, "igQuaternionfList")
                vl = R(g, src, 2)
                sl = R(g, src, 4)
                times = np.array(g.data_list(tl, "q"), np.float64) / 1e9 if tl is not None else None
                if times is None or len(times) == 0:
                    continue
                q = (np.array(g.data_list(ql, "4f"), np.float64).reshape(-1, 4)
                     if ql is not None else np.tile([0, 0, 0, 1.0], (len(times), 1)))
                t = np.array(g.data_list(vl, "3f"), np.float64).reshape(-1, 3) if vl is not None else None
                n = min(len(times), len(q), len(t) if t is not None else 1 << 30)
                scales = np.array(g.data_list(sl, "3f"), np.float64).reshape(-1, 3) if sl is not None else None
                A.tracks.append(Track(bone, times[:n], q[:n], t[:n] if t is not None else None, scales))
            elif src.is_a("igCompressedAnimationSequenceQS") or src.type == "igCompressedAnimationSequenceRaven":
                def quant(slot, width, base, factor):
                    lst = R(g, src, slot)
                    if lst is None:
                        return None
                    raw = np.asarray(g.data_list(lst, "H"), np.float64)
                    return (raw * factor + base).reshape(-1, width)
                times_raw = quant(4, 1, 0, 1)
                if times_raw is None or not len(times_raw):
                    continue
                # Xbox prototype getTimeAsLong at VA 0x217540:
                # bit 15 is sign, magnitude is shifted left by 23 nanoseconds.
                # Also confirmed in Batman Alchemy 2.5 debug XBE: encoder
                # VA 0x1764c2 sets 0x8000; VA 0x1764e7 shifts magnitude by 23.
                encoded = times_raw[:, 0].astype(np.uint16)
                times = (encoded & 0x7fff).astype(np.float64) * (2**23 / 1e9)
                times *= np.where(encoded & 0x8000, -1.0, 1.0)
                q = quant(3, 4, src.get(7, 0), src.get(8, 1))
                t = quant(2, 3, src.get(5, 0), src.get(6, 1))
                if q is None:
                    q = np.tile([0,0,0,1.0], (len(times),1))
                q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
                if len(q) != len(times) or (t is not None and len(t) != len(times)):
                    raise ValueError("QS channel count mismatch in " + str(bone))
                A.tracks.append(Track(bone, times, q, t))
                if dur == 0:
                    A.duration = max(A.duration, float(times[-1]))
            else:
                warnings.append("%s: unsupported track source %s" % (A.name, src.type))
        mask = R(g, an, 10)
        if mask is not None:
            memory = g.ref(mask.get(4, -1))
            words = np.frombuffer(memory.data, dtype="<u4") if memory is not None and getattr(memory, "data", None) else []
            enabled = {}
            for i, source_track in enumerate(g.obj_list(R(g, an, 5))):
                if source_track is not None:
                    enabled[source_track.get(2)] = i//32 < len(words) and bool(int(words[i//32]) & (1 << (i%32)))
            for track in A.tracks:
                if not enabled.get(track.bone, False):
                    track.trans = None
        # Missing masks in older broken exports are recoverable on import:
        # retain their stored curves so the corrected writer can restore the mask.
        anims.append(A)
    if g.version >= 10:
        from .animation2 import load
        anims.extend(load(g, warnings))
    return anims
