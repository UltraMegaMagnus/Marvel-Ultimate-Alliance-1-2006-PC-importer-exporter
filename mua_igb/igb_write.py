"""IGB (Alchemy) v6-v8 serializer.

Writes the exact inverse of igb.py. Class/field tables are taken from a template IGB that was
parsed with igb.IGB, so the output uses the game's own class definitions.
"""
import struct

from .igb import Obj, Mem, Ext, _FIXED

FLAG_INFO, FLAG_EXTERNAL, FLAG_SHARED, FLAG_POOLNAMES = 0x80000000, 0x40000000, 0x20000000, 0x10000000


class NewMem:
    """A memory block created by an exporter."""
    __slots__ = ("index", "data", "tag", "align", "refc")

    def __init__(self, data=b"", tag=0, align=-1, refc=1):
        self.index, self.data, self.tag, self.align, self.refc = -1, bytes(data), tag, align, refc

    @property
    def size(self):
        return len(self.data)


def _pad4(n):
    return (n + 3) & ~3


class _Pool:
    """Name pool: raw entries (bytes as stored, so originals stay byte-exact) + new names."""
    def __init__(self, raw_names, trust_pidx=False):
        self.trust_pidx = trust_pidx
        self.raw = list(raw_names)
        self.lookup = {}
        for i, b in enumerate(self.raw):
            self.lookup.setdefault(b.split(b"\0")[0].decode("latin1"), i)

    def intern(self, s):
        pidx = getattr(s, "pidx", None)
        if self.trust_pidx and pidx is not None and pidx < len(self.raw):
            return pidx
        s = str(s or "")
        if s not in self.lookup:
            self.lookup[s] = len(self.raw)
            self.raw.append(s.encode("latin1") + b"\0" if s else b"")
        return self.lookup[s]

    def pack(self):
        body = b"".join(struct.pack("<I", len(b)) + b for b in self.raw)
        return struct.pack("<II", 8 + len(body), len(self.raw)) + body


def _ref(v):
    if v is None:
        return -1
    if isinstance(v, int):
        return v
    return v.index


def _pack_field(kind, fsz, val, pool, version):
    if kind == "String":
        if version >= 8:
            return struct.pack("<i", pool.intern(val))
        b = (val or "").encode("latin1")
        b = b + b"\0" if b else b""
        return struct.pack("<i", len(b)) + b
    if kind in ("ObjectRef", "MemoryRef", "RawRef"):
        return struct.pack("<i", _ref(val))
    if isinstance(val, list):                      # array of object references (e.g. cube-map faces)
        return struct.pack("<%di" % len(val), *[_ref(v) for v in val]).ljust(fsz, b"\0")
    if kind in _FIXED:
        fmt = _FIXED[kind]
        if isinstance(val, (tuple, list)):
            return struct.pack("<" + fmt, *val)
        return struct.pack("<" + fmt, val)
    return bytes(val) if isinstance(val, (bytes, bytearray)) else bytes(fsz)


def _pack_object(tpl, ob, pool):
    meta = ob.meta
    out = bytearray()
    nf = ob.nf if ob.nf is not None else len(meta.fields)
    for (ti, slot, fsz) in meta.fields[:nf]:
        kind = tpl.metafields[ti].short
        raw = _pack_field(kind, fsz, ob.fields.get(slot, 0), pool, tpl.version)
        out += raw + b"\0" * (_pad4(len(raw)) - len(raw))
    out += ob.tail
    return struct.pack("<II", tpl.metaobjs.index(meta) if not hasattr(meta, "index") else meta.index, 8 + len(out)) + bytes(out)


def _pack_entries(tpl, entries, pool_empty=None):
    """entries: list of (meta object name, {slot: value})."""
    buf = bytearray()
    by_name = {m.name: m for m in tpl.metaobjs}
    for name, vals in entries:
        m = by_name[name]
        body = bytearray()
        for (ti, slot, fsz) in m.fields:
            fmt = {4: "i", 2: "h", 1: "B"}[fsz]
            body += struct.pack("<" + fmt, vals.get(slot, 0)) + b"\0" * (_pad4(fsz) - fsz)
        buf += struct.pack("<II", m.index, 8 + len(body)) + bytes(body)
    return bytes(buf)


def write_igb(path, tpl, items, info_item, entries=None, index=None, names=None):
    """Serialize `items` (Obj / Mem / NewMem, in directory order) using the class tables of `tpl`.

    entries/index: pre-built directory entries (round trip); otherwise one entry per item is made.
    """
    for i, it in enumerate(items):
        it.index = i
    pool = _Pool(names if names is not None else [], trust_pidx=entries is not None)
    obj_buf, mem_buf = bytearray(), bytearray()
    n_obj = n_mem = 0
    for it in items:
        if isinstance(it, Ext):
            continue
        if isinstance(it, (Mem, NewMem)):
            data = bytes(it.data)
            mem_buf += data + b"\0" * (_pad4(len(data)) - len(data))
            n_mem += 1
        else:
            obj_buf += _pack_object(tpl, it.meta, pool) if False else _pack_object(tpl, it, pool)
            n_obj += 1
    if entries is None:
        empty = pool.intern("")
        entries, index = [], []
        for i, it in enumerate(items):
            if isinstance(it, (Mem, NewMem)):
                entries.append(("igMemoryDirEntry", {2: empty, 7: len(it.data), 10: it.tag, 11: it.refc, 12: it.align, 13: -1}))
            else:
                entries.append(("igObjectDirEntry", {2: empty, 11: it.meta.index, 12: -1}))
            index.append(i)
    entry_buf = _pack_entries(tpl, entries)
    index_buf = struct.pack("<II", 8 + 2 * len(index), len(index)) + struct.pack("<%dH" % len(index), *index)
    index_buf += b"\0" * (_pad4(len(index_buf)) - len(index_buf)) if False else b""
    info_buf = struct.pack("<I", _ref(info_item)) if tpl.flags_raw & FLAG_INFO else b""
    name_buf = pool.pack() if tpl.version >= 8 else b""
    hdr = struct.pack("<12I", len(entry_buf), len(entries), len(tpl.raw_mo), len(tpl.metaobjs),
                      len(obj_buf), n_obj, len(mem_buf), n_mem, len(tpl.raw_mf), len(tpl.metafields),
                      0xFADA, tpl.flags_raw)
    with open(path, "wb") as f:
        f.write(hdr)
        f.write(name_buf)
        f.write(tpl.raw_mf)
        f.write(tpl.raw_align)
        f.write(tpl.raw_mo)
        f.write(tpl.raw_ext)
        f.write(tpl.raw_pool)
        f.write(entry_buf)
        if tpl.version >= 6:
            f.write(index_buf)
            f.write(info_buf)
        f.write(bytes(obj_buf))
        f.write(bytes(mem_buf))
        if tpl.version < 6:
            f.write(info_buf)


def roundtrip(src_path, dst_path):
    """Parse + rewrite a file with its own entries; returns True when the bytes are identical."""
    from .igb import IGB
    g = IGB(src_path)
    write_igb(dst_path, g, g.objects, g.info_index, entries=g.entries, index=g.index, names=g.names_raw)
    return open(src_path, "rb").read() == open(dst_path, "rb").read()


# ------------------------------------------------------------------ meta tables
def pack_meta(metafields, metaobjs):
    """metafields: [(name, major, minor)], metaobjs: [(name, major, minor, parent_idx, sc, [(ti, slot, size)])]
    -> (mf_buffer, mo_buffer) exactly as stored in a file."""
    st, dyn = bytearray(), bytearray()
    for name, mj, mn in metafields:
        b = name.encode("latin1") + b"\0"
        st += struct.pack("<III", len(b), mj, mn)
        dyn += b
    mf = bytes(st + dyn)
    st, dyn = bytearray(), bytearray()
    for name, mj, mn, par, sc, fields in metaobjs:
        b = name.encode("latin1") + b"\0"
        b += b"\0" * (len(b) & 1)                          # class names are padded to an even length
        st += struct.pack("<IIIIiI", len(b), mj, mn, len(fields), par, sc)
        dyn += b
        for ti, slot, size in fields:
            dyn += struct.pack("<HHH", ti, slot, size)
    mo = bytes(st + dyn)
    return mf, mo


def meta_tuples(g):
    mfs = [(m.name, m.major, m.minor) for m in g.metafields]
    mos = [(m.name, m.major, m.minor, m.parent_idx, m.sc, list(m.fields)) for m in g.metaobjs]
    return mfs, mos
