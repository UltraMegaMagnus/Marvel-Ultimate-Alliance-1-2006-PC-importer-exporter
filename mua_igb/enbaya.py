"""Enbaya animation stream decoder.

Python port of the decoding half of korenkonder/enbrip (src/enbaya.c),
MIT License, Copyright (c) 2020-2025 korenkonder.
https://github.com/korenkonder/enbrip

Decodes an igEnbayaAnimationSource blob into per-track, per-sample
(quaternion xyzw, translation xyz) values, one sample per 1/sample_rate s.
"""
import math
import struct

_SH2 = (6, 4, 2, 0)
_SH4 = (4, 0)
_V2 = (0, 1, 0, -1)
_V4 = (0, 8, 2, 3, 4, 5, 6, 7, -8, -7, -6, -5, -4, -3, -2, -9)

HEADER_SIZE = 80  # 19 u32 fields + 1 placeholder u32


class EnbayaError(Exception):
    pass


class _Reader:
    """Sequential typed reader over a slice of the blob."""
    __slots__ = ("d", "p", "c")

    def __init__(self, data, start):
        self.d, self.p, self.c = data, start, 0


def decode(blob):
    """Return (sample_rate, duration, tracks) where
    tracks[track][sample] = (qx, qy, qz, qw, tx, ty, tz)."""
    if len(blob) < HEADER_SIZE:
        raise EnbayaError("blob too small")
    h = struct.unpack_from("<II ff I 14I", blob, 0)
    (_sig, ntracks, qerr, duration, rate,
     l_ii2, l_ii8, l_ii16, l_ii32, l_i2, l_i4, l_i8, l_i16, l_i32,
     l_u2, l_u8, l_u16, l_u32, l_flags) = h[:19]
    if not (1 <= ntracks <= 1000 and rate >= 1 and duration >= 0):
        raise EnbayaError("bad header")

    # stream order: [init_i32][i32][u32][init_i16][i16][u16][init_i2][init_i8][i2][i4][i8][u2][u8][flags]
    o_init_i32 = HEADER_SIZE
    o_i32 = o_init_i32 + l_ii32
    o_u32 = o_i32 + l_i32
    o_init_i16 = o_u32 + l_u32
    o_i16 = o_init_i16 + l_ii16
    o_u16 = o_i16 + l_i16
    o_init_i2 = o_u16 + l_u16
    o_init_i8 = o_init_i2 + l_ii2
    o_i2 = o_init_i8 + l_ii8
    o_i4 = o_i2 + l_i2
    o_i8 = o_i4 + l_i4
    o_u2 = o_i8 + l_i8
    o_u8 = o_u2 + l_u2
    o_flags = o_u8 + l_u8
    end = o_flags + l_flags
    if end > len(blob):
        raise EnbayaError("truncated stream (%d > %d)" % (end, len(blob)))

    b = blob
    S = struct.unpack_from

    # --- cursors (position + sub-byte counter) ---
    ini = [o_init_i2, 0, o_init_i8, o_init_i16, o_init_i32]   # i2 pos, i2 counter, i8, i16, i32
    trk = [o_i2, 0, o_i4, 0, o_i8, o_i16, o_i32]              # i2 pos, ctr, i4 pos, ctr, i8, i16, i32
    sta = [o_u2, 0, o_u8, o_u16, o_u32]                       # u2 pos, ctr, u8, u16, u32

    def init_decode():
        if ini[1] == 4:
            ini[1] = 0
            ini[0] += 1
        v = (b[ini[0]] >> _SH2[ini[1]]) & 3
        ini[1] += 1
        if v == 1:
            v = S("<b", b, ini[2])[0]; ini[2] += 1
        elif v == 2:
            v = S("<h", b, ini[3])[0]; ini[3] += 2
        elif v == 3:
            v = S("<i", b, ini[4])[0]; ini[4] += 4
        return v

    def track_decode():
        if trk[1] == 4:
            trk[1] = 0
            trk[0] += 1
        v = (b[trk[0]] >> _SH2[trk[1]]) & 3
        trk[1] += 1
        if v == 2:
            if trk[3] == 2:
                trk[3] = 0
                trk[2] += 1
            v = (b[trk[2]] >> _SH4[trk[3]]) & 0xF
            trk[3] += 1
            if v == 0:
                v = S("<b", b, trk[4])[0]; trk[4] += 1
                if v == 0:
                    v = S("<h", b, trk[5])[0]; trk[5] += 2
                    if v == 0:
                        v = S("<i", b, trk[6])[0]; trk[6] += 4
                elif 0 < v < 9:
                    v += 0x7F
                elif -9 < v < 0:
                    v -= 0x80
            else:
                v = _V4[v]
        else:
            v = _V2[v]
        return v

    def state_decode():
        if sta[1] == 4:
            sta[1] = 0
            sta[0] += 1
        v = (b[sta[0]] >> _SH2[sta[1]]) & 3
        sta[1] += 1
        if v == 1:
            v = b[sta[2]]; sta[2] += 1
        elif v == 2:
            v = S("<H", b, sta[3])[0]; sta[3] += 2
        elif v == 3:
            v = S("<I", b, sta[4])[0]; sta[4] += 4
        return v

    # --- initial values ---
    cur = []      # accumulated integer deltas, 7 per track
    out = [[] for _ in range(ntracks)]
    base = []     # last decoded absolute value (floats), 7 per track
    flags = []
    for t in range(ntracks):
        vals = [float(init_decode()) for _ in range(7)]
        q = [v * qerr for v in vals[:4]]
        n = math.sqrt(sum(x * x for x in q)) or 1.0
        q = [x / n for x in q]
        tr = [v * qerr for v in vals[4:]]
        base.append(q + tr)
        out[t].append(tuple(q + tr))
        cur.append([0.0] * 7)
        flags.append(b[o_flags + t])

    # state machine
    next_step = state_decode()
    nsamples = int(math.ceil(duration * rate - 1e-6)) + 1
    comps = ntracks * 7
    sample = 0
    while len(out[0]) < nsamples:
        if sample > 0:
            i = 0
            while i < comps:
                if next_step == 0:
                    flags[i // 7] ^= 1 << (i % 7)
                    next_step = state_decode()
                    i += 1
                else:
                    t = min(next_step, comps - i)
                    i += t
                    next_step -= t
        for t in range(ntracks):
            f = flags[t]
            c = cur[t]
            if f:
                for j in range(7):
                    if f & (1 << j):
                        c[j] += track_decode()
            bt = base[t]
            q = [bt[k] + c[k] * qerr for k in range(4)]
            n = math.sqrt(sum(x * x for x in q)) or 1.0
            q = [x / n for x in q]
            tr = [bt[4 + k] + c[4 + k] * qerr for k in range(3)]
            nb = q + tr
            base[t] = nb          # normalized result feeds the next step
            out[t].append(tuple(nb))
        sample += 1
    return rate, duration, out
