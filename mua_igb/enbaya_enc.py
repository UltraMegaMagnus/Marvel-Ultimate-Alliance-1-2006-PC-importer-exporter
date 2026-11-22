"""Enbaya animation stream encoder (inverse of enbaya.decode).

Closed loop: every sample is quantised against the value the decoder will actually reconstruct,
so quantisation error never accumulates. Stream layout and codes follow korenkonder/enbrip
(MIT, Copyright (c) 2020-2025 korenkonder), see LICENSE-enbrip.txt.
"""
import math
import struct

import numpy as np

SIGNATURE = 269126572
QERR = 0.0004
_V4 = (0, 8, 2, 3, 4, 5, 6, 7, -8, -7, -6, -5, -4, -3, -2, -9)
_V4_INV = {v: i for i, v in enumerate(_V4) if i > 0}


class _Bits:
    """Packs fixed-width codes MSB-first into bytes (like the decoder's shift tables)."""
    def __init__(self, width):
        self.width, self.per = width, 8 // width
        self.buf, self.n = bytearray(), 0

    def put(self, v):
        if self.n % self.per == 0:
            self.buf.append(0)
        shift = 8 - self.width * (self.n % self.per + 1)
        self.buf[-1] |= (v & ((1 << self.width) - 1)) << shift
        self.n += 1


def _init_code(v, c2, i8, i16, i32):
    if v == 0:
        c2.put(0)
    elif -128 <= v <= 127:
        c2.put(1)
        i8.extend(struct.pack("<b", v))
    elif -32768 <= v <= 32767:
        c2.put(2)
        i16.extend(struct.pack("<h", v))
    else:
        c2.put(3)
        i32.extend(struct.pack("<i", v))


def _track_code(d, c2, c4, i8, i16, i32):
    if d == 0:
        c2.put(0)
    elif d == 1:
        c2.put(1)
    elif d == -1:
        c2.put(3)
    else:
        c2.put(2)
        if d in _V4_INV:
            c4.put(_V4_INV[d])
            return
        c4.put(0)
        if 9 <= d <= 127 or -128 <= d <= -9:
            i8.extend(struct.pack("<b", d))
        elif 128 <= d <= 135:
            i8.extend(struct.pack("<b", d - 127))
        elif -136 <= d <= -129:
            i8.extend(struct.pack("<b", d + 128))
        elif -32768 <= d <= 32767:
            i8.extend(b"\0")
            i16.extend(struct.pack("<h", d))
        else:
            i8.extend(b"\0")
            i16.extend(struct.pack("<h", 0))
            i32.extend(struct.pack("<i", d))


def encode(samples, rate, duration, qerr=QERR, const_tol=None):
    """samples: array (tracks, n, 7) = quaternion xyzw (unit) + translation xyz.
    Returns the blob for an igEnbayaAnimationSource."""
    S = np.asarray(samples, np.float64)
    T, n, _ = S.shape
    const_tol = qerr * 0.75 if const_tol is None else const_tol
    # flags: components that actually change
    flags = []
    for t in range(T):
        f = 0
        for j in range(7):
            if np.abs(S[t, :, j] - S[t, 0, j]).max() > const_tol:
                f |= 1 << j
        flags.append(f)
    ii2, ii8, ii16, ii32 = _Bits(2), bytearray(), bytearray(), bytearray()
    t2, t4, t8, t16, t32 = _Bits(2), _Bits(4), bytearray(), bytearray(), bytearray()
    base = []
    for t in range(T):
        ints = [int(round(S[t, 0, j] / qerr)) for j in range(7)]
        for v in ints:
            _init_code(v, ii2, ii8, ii16, ii32)
        q = np.array([ints[k] * qerr for k in range(4)])
        q /= (np.linalg.norm(q) or 1.0)
        base.append(np.concatenate([q, [ints[k] * qerr for k in range(4, 7)]]))
    vel = np.zeros((T, 7), np.int64)
    for s in range(1, n):
        for t in range(T):
            f = flags[t]
            b = base[t]
            for j in range(7):
                if f & (1 << j):
                    newv = int(round((S[t, s, j] - b[j]) / qerr))
                    _track_code(newv - int(vel[t, j]), t2, t4, t8, t16, t32)
                    vel[t, j] = newv
            q = b[:4] + vel[t, :4] * qerr
            q = q / (np.linalg.norm(q) or 1.0)
            tr = b[4:] + vel[t, 4:] * qerr
            base[t] = np.concatenate([q, tr])
    state2 = bytearray([0xC0 | 0])                      # one state code: 3 (u32) -> "never toggle"
    state32 = struct.pack("<I", 0x7FFFFFFF)
    streams = [
        ("init_i32", bytes(ii32)), ("i32", bytes(t32)), ("u32", state32), ("init_i16", bytes(ii16)),
        ("i16", bytes(t16)), ("u16", b""), ("init_i2", bytes(ii2.buf)), ("init_i8", bytes(ii8)),
        ("i2", bytes(t2.buf)), ("i4", bytes(t4.buf)), ("i8", bytes(t8)), ("u2", bytes(state2)),
        ("u8", b""), ("flags", bytes(flags)),
    ]
    d = dict(streams)
    hdr = struct.pack("<IIffI14I", SIGNATURE, T, qerr, float(duration), int(rate),
                      len(d["init_i2"]), len(d["init_i8"]), len(d["init_i16"]), len(d["init_i32"]),
                      len(d["i2"]), len(d["i4"]), len(d["i8"]), len(d["i16"]), len(d["i32"]),
                      len(d["u2"]), len(d["u8"]), len(d["u16"]), len(d["u32"]), len(d["flags"]))
    hdr += struct.pack("<I", 0)                         # placeholder pointer
    return hdr + b"".join(v for _, v in streams)
