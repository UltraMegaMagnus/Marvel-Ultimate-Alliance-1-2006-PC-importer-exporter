"""Tiny numpy DXT1/DXT3/DXT5 decoder -> float32 RGBA array (h, w, 4), top row first."""
import numpy as np


def _rgb565(c):
    r = ((c >> 11) & 31).astype(np.float32) * (255.0 / 31.0)
    g = ((c >> 5) & 63).astype(np.float32) * (255.0 / 63.0)
    b = (c & 31).astype(np.float32) * (255.0 / 31.0)
    return np.stack([r, g, b], axis=-1)


def _color_blocks(blk, dxt1):
    """blk: (n, 8) uint8 -> (n, 16, 4) float32 RGBA 0..255"""
    c0 = blk[:, 0].astype(np.uint16) | (blk[:, 1].astype(np.uint16) << 8)
    c1 = blk[:, 2].astype(np.uint16) | (blk[:, 3].astype(np.uint16) << 8)
    p0, p1 = _rgb565(c0), _rgb565(c1)
    pal = np.zeros((len(blk), 4, 4), np.float32)
    pal[:, :, 3] = 255.0
    pal[:, 0, :3], pal[:, 1, :3] = p0, p1
    if dxt1:
        four = (c0 > c1)[:, None]
        pal[:, 2, :3] = np.where(four, (2 * p0 + p1) / 3.0, (p0 + p1) / 2.0)
        pal[:, 3, :3] = np.where(four, (p0 + 2 * p1) / 3.0, 0.0)
        pal[:, 3, 3] = np.where(four[:, 0], 255.0, 0.0)
    else:
        pal[:, 2, :3] = (2 * p0 + p1) / 3.0
        pal[:, 3, :3] = (p0 + 2 * p1) / 3.0
    bits = (blk[:, 4].astype(np.uint32) | (blk[:, 5].astype(np.uint32) << 8) |
            (blk[:, 6].astype(np.uint32) << 16) | (blk[:, 7].astype(np.uint32) << 24))
    idx = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))[None, :]) & 3
    return np.take_along_axis(pal, idx[:, :, None].astype(np.int64), axis=1)


def _alpha_dxt5(blk):
    a0, a1 = blk[:, 0].astype(np.float32), blk[:, 1].astype(np.float32)
    pal = np.zeros((len(blk), 8), np.float32)
    pal[:, 0], pal[:, 1] = a0, a1
    gt = a0 > a1
    for i in range(1, 7):
        pal[:, i + 1] = np.where(gt, ((7 - i) * a0 + i * a1) / 7.0,
                                 np.where(i <= 5, ((6 - i) * a0 + i * a1) / 5.0, 0.0))
    pal[:, 6] = np.where(gt, pal[:, 6], 0.0)
    pal[:, 7] = np.where(gt, pal[:, 7], 255.0)
    bits = np.zeros(len(blk), np.uint64)
    for i in range(6):
        bits |= blk[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    idx = (bits[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64))[None, :]) & np.uint64(7)
    return np.take_along_axis(pal, idx.astype(np.int64), axis=1)


def decode(data, w, h, kind):
    """kind: 'dxt1' | 'dxt3' | 'dxt5'. Returns (h, w, 4) float32 in 0..1."""
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    size = 8 if kind == "dxt1" else 16
    n = bw * bh
    raw = np.frombuffer(data, np.uint8, count=n * size).reshape(n, size)
    if kind == "dxt1":
        px = _color_blocks(raw, True)
    else:
        px = _color_blocks(raw[:, 8:], False)
        if kind == "dxt3":
            a = np.zeros((n, 16), np.float32)
            for i in range(8):
                a[:, 2 * i] = (raw[:, i] & 15) * 17.0
                a[:, 2 * i + 1] = (raw[:, i] >> 4) * 17.0
            px[:, :, 3] = a
        else:
            px[:, :, 3] = _alpha_dxt5(raw[:, :8])
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return (img[:h, :w] / 255.0).astype(np.float32)


# ----------------------------------------------------------------- uncompressed / generic
PFMT_DXT = {13: "dxt1", 14: "dxt1", 15: "dxt3", 16: "dxt5"}


def decode_pixels(pfmt, data, w, h, bytes_per_row=None):
    """Decode an igImage pixel buffer to float32 RGBA (h, w, 4), top row first.
    Returns (array, is_dxt) or (None, reason)."""
    if pfmt in (21, 34):
        # GX CMPR: four 4x4 blocks per 8x8 tile, big-endian endpoints,
        # with the leftmost pixel in the high two bits of each row byte.
        mw, mh = (w + 7) // 8, (h + 7) // 8
        raw = np.frombuffer(data, np.uint8, count=mw*mh*32).reshape(-1, 8).copy()
        if pfmt == 21:
            raw[:, :4] = raw[:, [1, 0, 3, 2]]
            v = raw[:, 4:].copy()
            raw[:, 4:] = ((v & 3) << 6) | ((v & 12) << 2) | ((v & 48) >> 2) | ((v & 192) >> 6)
        # Format 34 in the supplied MUA2 files uses little-endian DXT blocks
        # in the same 8x8 tile order; format 21 is native GX CMPR.
        px = _color_blocks(raw, True).reshape(mh, mw, 2, 2, 4, 4, 4)
        img = px.transpose(0, 2, 4, 1, 3, 5, 6).reshape(mh*8, mw*8, 4)
        return (img[:h, :w] / 255).astype(np.float32), True
    if pfmt in PFMT_DXT:
        return decode(data, w, h, PFMT_DXT[pfmt]), True
    bpp = {0: 1, 1: 1, 3: 2, 5: 3, 7: 4, 8: 2, 9: 2, 10: 2}.get(pfmt)
    if bpp is None:
        return None, "unsupported pixel format %s" % pfmt
    stride = bytes_per_row if bytes_per_row and bytes_per_row >= w * bpp else w * bpp
    need = stride * h
    if len(data) < need:
        return None, "truncated pixel data (%d < %d)" % (len(data), need)
    raw = np.frombuffer(data, np.uint8, count=need).reshape(h, stride)[:, :w * bpp]
    out = np.ones((h, w, 4), np.float32)
    if pfmt == 0:
        out[..., :3] = (raw.astype(np.float32) / 255.0)[..., None]
    elif pfmt == 1:
        out[..., :3] = 1.0
        out[..., 3] = raw / 255.0
    elif pfmt == 3:
        px = raw.reshape(h, w, 2).astype(np.float32) / 255.0
        out[..., :3] = px[..., :1]
        out[..., 3] = px[..., 1]
    elif pfmt == 5:                       # stored B,G,R in memory (PC little-endian convention)
        px = raw.reshape(h, w, 3).astype(np.float32) / 255.0
        out[..., 0], out[..., 1], out[..., 2] = px[..., 2], px[..., 1], px[..., 0]
    elif pfmt == 7:
        px = raw.reshape(h, w, 4).astype(np.float32) / 255.0
        out[...] = px
    else:                                  # 16-bit packed
        v = raw.reshape(h, w, 2).astype(np.uint16)
        v = v[..., 0] | (v[..., 1] << 8)
        if pfmt == 8:
            out[..., 0] = ((v >> 11) & 31) / 31.0; out[..., 1] = ((v >> 6) & 31) / 31.0
            out[..., 2] = ((v >> 1) & 31) / 31.0; out[..., 3] = (v & 1)
        elif pfmt == 9:
            out[..., 0] = ((v >> 12) & 15) / 15.0; out[..., 1] = ((v >> 8) & 15) / 15.0
            out[..., 2] = ((v >> 4) & 15) / 15.0; out[..., 3] = (v & 15) / 15.0
        else:
            out[..., 0] = ((v >> 11) & 31) / 31.0; out[..., 1] = ((v >> 5) & 63) / 63.0
            out[..., 2] = (v & 31) / 31.0
    return out, False


# ------------------------------------------------------------------ encoder
def _to565(rgb):
    r = np.clip(np.round(rgb[..., 0] * 31), 0, 31).astype(np.uint16)
    g = np.clip(np.round(rgb[..., 1] * 63), 0, 63).astype(np.uint16)
    b = np.clip(np.round(rgb[..., 2] * 31), 0, 31).astype(np.uint16)
    return (r << 11) | (g << 5) | b


def _from565(c):
    return np.stack([((c >> 11) & 31) / 31.0, ((c >> 5) & 63) / 63.0, (c & 31) / 31.0], axis=-1)


def _blocks(img):
    h, w = img.shape[:2]
    H, W = (h + 3) // 4 * 4, (w + 3) // 4 * 4
    if (H, W) != (h, w):
        img = np.pad(img, ((0, H - h), (0, W - w), (0, 0)), mode="edge")
    return img.reshape(H // 4, 4, W // 4, 4, -1).transpose(0, 2, 1, 3, 4).reshape(-1, 16, img.shape[-1])


def _encode_color(rgb16, allow_alpha=False):
    """(n,16,3) float -> (n,8) uint8 colour block (4-colour mode)."""
    n = len(rgb16)
    lo, hi = rgb16.min(axis=1), rgb16.max(axis=1)
    inset = (hi - lo) / 16.0                         # slight inset improves fit
    c0 = _to565(np.clip(hi - inset, 0, 1))
    c1 = _to565(np.clip(lo + inset, 0, 1))
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    same = c0 == c1
    c0 = np.where(same & (c0 < 0xFFFF), c0 + 1, c0)   # force 4-colour mode (c0 > c1)
    p0, p1 = _from565(c0), _from565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], axis=1)           # (n,4,3)
    d = ((rgb16[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)                    # (n,16,4)
    idx = d.argmin(-1).astype(np.uint32)
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))[None, :]).sum(1).astype(np.uint32)
    out = np.zeros((n, 8), np.uint8)
    out[:, 0], out[:, 1] = c0 & 255, c0 >> 8
    out[:, 2], out[:, 3] = c1 & 255, c1 >> 8
    for i in range(4):
        out[:, 4 + i] = (bits >> (8 * i)) & 255
    return out


def _encode_alpha5(a16):
    """(n,16) float 0..1 -> (n,8) uint8 DXT5 alpha block."""
    n = len(a16)
    a = np.round(a16 * 255).astype(np.float32)
    a0, a1 = a.max(1), a.min(1)                       # a0 > a1 -> 8-level interpolation
    same = a0 == a1
    a0 = np.where(same & (a0 < 255), a0 + 1, a0)
    pal = np.stack([a0, a1] + [((7 - i) * a0 + i * a1) / 7 for i in range(1, 7)], axis=1)   # (n,8)
    idx = np.abs(a[:, :, None] - pal[:, None, :]).argmin(-1).astype(np.uint64)
    bits = (idx << (3 * np.arange(16, dtype=np.uint64))[None, :]).sum(1).astype(np.uint64)
    out = np.zeros((n, 8), np.uint8)
    out[:, 0], out[:, 1] = a0.astype(np.uint8), a1.astype(np.uint8)
    for i in range(6):
        out[:, 2 + i] = ((bits >> np.uint64(8 * i)) & np.uint64(255)).astype(np.uint8)
    return out


def encode(rgba, kind, swap_rb=True):
    """float32 RGBA (h,w,4), top row first -> DXT bytes. kind: 'dxt1' | 'dxt5'.
    swap_rb: MUA PC stores the colour endpoints with red and blue swapped."""
    img = np.asarray(rgba, np.float32)
    if swap_rb:
        img = img[..., [2, 1, 0, 3]]
    blk = _blocks(img)
    col = _encode_color(blk[:, :, :3])
    if kind == "dxt1":
        return col.tobytes()
    al = _encode_alpha5(blk[:, :, 3])
    return np.concatenate([al, col], axis=1).tobytes()
