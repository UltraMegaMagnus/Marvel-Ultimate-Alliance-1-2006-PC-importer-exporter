"""Minimal, dependency-free reader for Alchemy IGB files (v5-v8 and v10, little endian).

File layout (all u32 little endian):
  header (0x30): entry(size,count) metaobj(size,count) obj(size,count)
                 mref(size,count) metafield(size,count) magic(0xFADA) ver|flags
  [v8+]  name pool           (size,count) then (len, bytes)*
  meta-field buffer          count*12 static (nameLen,major,minor) + names
  alignment buffer           (first u32 = size)
  meta-object buffer         count*24 static + (name + fields*6) dynamic
  [external dirs] [memory pool names]
  entry buffer               (typeIdx,size,<fields>)  -> directory entries
  index buffer               (size,count) u16 indices into entries   (v6+)
  [info list index u32]
  object buffer              (typeIdx,size,<fields>) per object entry
  memory-ref buffer          raw blocks, 4-byte aligned
"""
import struct

FLAG_INFO = 0x80000000
FLAG_EXTERNAL = 0x40000000
FLAG_SHARED = 0x20000000
FLAG_POOLNAMES = 0x10000000


class PStr(str):
    """String that remembers its name-pool index (keeps round trips byte-exact)."""
    pidx = None


class MetaField:
    __slots__ = ("name", "short", "major", "minor")

    def __init__(self, name, major=1, minor=0):
        self.name, self.major, self.minor = name, major, minor
        s = name[2:] if name.startswith("ig") else name
        self.short = s[:-9] if s.endswith("MetaField") else s


class MetaObject:
    __slots__ = ("index", "name", "fields", "parent_idx", "parent", "major", "minor", "sc")

    def __init__(self, index, name, fields, parent_idx, major=1, minor=0, sc=0):
        self.index, self.name, self.fields = index, name, fields
        self.parent_idx, self.parent = parent_idx, None
        self.major, self.minor, self.sc = major, minor, sc

    def is_a(self, name):
        m = self
        while m is not None:
            if m.name == name:
                return True
            m = m.parent
        return False


class Obj:
    """A deserialized object. fields: slot -> value, kinds: slot -> short type."""
    __slots__ = ("index", "meta", "fields", "kinds", "nf", "tail")

    def __init__(self, index, meta):
        self.index, self.meta = index, meta
        self.fields, self.kinds = {}, {}
        self.nf, self.tail = None, b""

    @property
    def type(self):
        return self.meta.name

    def is_a(self, name):
        return self.meta.is_a(name)

    def get(self, slot, default=None):
        return self.fields.get(slot, default)

    def first(self, kind, default=None):
        """First field (lowest slot) of a given type kind."""
        for s in sorted(self.fields):
            if self.kinds[s] == kind:
                return self.fields[s]
        return default

    def all_of(self, kind):
        return [(s, self.fields[s]) for s in sorted(self.fields) if self.kinds[s] == kind]

    def __repr__(self):
        return "<%s #%d>" % (self.meta.name, self.index)


class Ext:
    """Directory entry that points at an object stored in another file (igExternalDirEntry); no data here."""
    __slots__ = ("index", "vals")

    def __init__(self, index, vals):
        self.index, self.vals = index, vals

    def __repr__(self):
        return "<Ext #%d %s>" % (self.index, self.vals)


class Mem:
    __slots__ = ("index", "size", "type_idx", "data")

    def __init__(self, index, size, type_idx):
        self.index, self.size, self.type_idx, self.data = index, size, type_idx, b""

    def __repr__(self):
        return "<Mem #%d %dB>" % (self.index, self.size)


_FIXED = {
    "Bool": "B", "Char": "b", "UnsignedChar": "B", "Short": "h", "UnsignedShort": "H",
    "Int": "i", "UnsignedInt": "I", "Long": "q", "UnsignedLong": "Q", "Float": "f",
    "Double": "d", "Vec2f": "2f", "Vec3f": "3f", "Vec4f": "4f", "Quaternionf": "4f",
    "Matrix44f": "16f", "Enum": "i", "Handle": "I", "BitField": "I",
    "ObjectRef": "i", "MemoryRef": "i", "RawRef": "i", "Vec3ucMeta": "3B",
}


class IGB:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        self.path = path
        self._parse()

    # ------------------------------------------------------------------ parse
    def _u32(self, p):
        return struct.unpack_from("<I", self.data, p)[0]

    def _parse(self):
        d = self.data
        h = struct.unpack_from("<12I", d, 0)
        (self.entry_size, self.entry_count, mo_size, mo_count, obj_size, obj_count,
         mref_size, mref_count, mf_size, mf_count, magic, verflags) = h
        if magic != 0xFADA:
            raise ValueError("not a little-endian IGB (magic %#x)" % magic)
        self.version = verflags & 0xFFFF
        flags = verflags & 0xFFFF0000
        p = 0x30
        self.names = []
        self.names_raw = []
        if self.version >= 8:
            size, cnt = struct.unpack_from("<II", d, p)
            q = p + 8
            for _ in range(cnt):
                n = self._u32(q)
                self.names_raw.append(bytes(d[q + 4:q + 4 + n]))
                self.names.append(bytes(d[q + 4:q + 4 + n]).split(b"\0")[0].decode("latin1"))
                q += 4 + n
            p += size
        self.flags_raw = verflags
        # meta-fields
        self.metafields = []
        buf = d[p:p + mf_size]
        self.raw_mf = bytes(buf)
        dyn = mf_count * 12
        for i in range(mf_count):
            n, mj, mn = struct.unpack_from("<III", buf, i * 12)
            nm = bytes(buf[dyn:dyn + n]).split(b"\0")[0].decode("latin1")
            dyn += n
            self.metafields.append(MetaField(nm, mj, mn))
        p += mf_size
        # alignment buffer
        self.raw_align = bytes(d[p:p + self._u32(p)]) if self.version < 10 else b""
        p += len(self.raw_align)
        # meta-objects
        buf = d[p:p + mo_size]
        self.raw_mo = bytes(buf)
        dyn = mo_count * 24
        self.metaobjs = []
        for i in range(mo_count):
            n, mj, mn, nf, par, sc = struct.unpack_from("<IIIIiI", buf, i * 24)
            nm = bytes(buf[dyn:dyn + n]).split(b"\0")[0].decode("latin1")
            dyn += n
            fields = []
            for _ in range(nf):
                fields.append(struct.unpack_from("<HHH", buf, dyn))
                dyn += 6
            self.metaobjs.append(MetaObject(i, nm, fields, par, mj, mn, sc))
        for m in self.metaobjs:
            if 0 <= m.parent_idx < len(self.metaobjs):
                m.parent = self.metaobjs[m.parent_idx]
        p += mo_size
        self.raw_ext = self.raw_pool = b''
        if flags & FLAG_EXTERNAL:
            self.raw_ext = bytes(d[p:p + self._u32(p)])
            p += self._u32(p)
        if flags & FLAG_POOLNAMES:
            self.raw_pool = bytes(d[p:p + self._u32(p)])
            p += self._u32(p)
        # Version 10 stores a shared-object mapping after the name pools.
        if self.version >= 10 and flags & FLAG_SHARED:
            p += self._u32(p)
        # entries
        shift = 1 if self.version <= 5 else 0
        ents = []
        q = p
        for _ in range(self.entry_count):
            t, sz = struct.unpack_from("<II", d, q)
            vals, o = {}, q + 8
            for (ti, slot, fsz) in self.metaobjs[t].fields:
                fmt = {4: "i", 2: "h", 1: "B"}[fsz]
                vals[slot] = struct.unpack_from("<" + fmt, d, o)[0]
                o += (fsz + 3) & ~3
            ents.append((self.metaobjs[t].name, vals))
            self.entry_types = getattr(self, 'entry_types', []) + [t]
            q += sz
        p += self.entry_size
        # index buffer (v6+ only; older files reference entries sequentially)
        if self.version >= 6:
            size, cnt = struct.unpack_from("<II", d, p)
            index = list(struct.unpack_from("<%dH" % cnt, d, p + 8))
            p += size
        else:
            index = list(range(self.entry_count))
        self.info_index = None
        if flags & FLAG_INFO:
            self.info_index = self._u32(p)
            p += 4
        # build object table
        self.entries = ents
        self.index = index
        self.objects = []
        for idx in index:
            name, v = ents[idx]
            if name.startswith("igExternal") or (self.version >= 10 and name == "igDirEntry"):
                self.objects.append(Ext(len(self.objects), v))
            elif name == "igMemoryDirEntry":
                self.objects.append(Mem(len(self.objects), v.get(7 + shift, 0), v.get(10 + shift, -1)))
            else:
                self.objects.append(Obj(len(self.objects), self.metaobjs[v.get(11 + shift, 0)]))
        # object buffer
        end = p + obj_size
        q = p
        for ob in self.objects:
            if not isinstance(ob, Obj):
                continue
            t, sz = struct.unpack_from("<II", d, q)
            meta = self.metaobjs[t]
            ob.meta = meta
            o, stop = q + 8, q + sz
            ob.nf = 0
            for (ti, slot, fsz) in meta.fields:
                kind = self.metafields[ti].short
                if o + 4 > stop and fsz >= 4 and kind != "String":
                    break
                ob.nf += 1
                if kind == "String":
                    if self.version >= 8:
                        pi = struct.unpack_from("<i", d, o)[0]
                        val = PStr(self.names[pi] if 0 <= pi < len(self.names) else "")
                        val.pidx = pi
                        adv = 4
                    else:
                        ln = struct.unpack_from("<i", d, o)[0]
                        val = bytes(d[o + 4:o + 4 + ln]).split(b"\0")[0].decode("latin1")
                        adv = 4 + ln
                elif kind in _FIXED:
                    fmt = _FIXED[kind]
                    r = struct.unpack_from("<" + fmt, d, o)
                    val = r[0] if len(r) == 1 else r
                    adv = fsz
                elif kind.endswith("Array") and kind[:-5] in _FIXED:
                    val = bytes(d[o:o + fsz])
                    adv = fsz
                else:
                    val = bytes(d[o:o + fsz])
                    adv = fsz
                ob.fields[slot - shift], ob.kinds[slot - shift] = val, kind
                o += (adv + 3) & ~3
            ob.tail = bytes(d[o:stop])
            q += sz
        p = end
        # memory blocks
        for ob in self.objects:
            if isinstance(ob, Mem):
                ob.data = bytes(d[p:p + ob.size])
                p += (ob.size + 3) & ~3

    # ---------------------------------------------------------------- helpers
    def ref(self, i):
        if i is None or i < 0 or i >= len(self.objects):
            return None
        return self.objects[i]

    def by_type(self, name):
        return [o for o in self.objects if isinstance(o, Obj) and o.is_a(name)]

    def obj_list(self, o):
        """igObjectList -> python list of objects (count at first Int, data MemoryRef)."""
        if not isinstance(o, Obj):
            return []
        cnt = o.first("Int", 0)
        m = self.ref(o.first("MemoryRef", -1))
        if not isinstance(m, Mem) or cnt <= 0:
            return []
        n = min(cnt, len(m.data) // 4)
        return [self.ref(i) for i in struct.unpack_from("<%di" % n, m.data)]

    def data_list(self, o, fmt):
        """igDataList subclass -> tuple list using a struct fmt per element."""
        if not isinstance(o, Obj):
            return []
        cnt = o.first("Int", 0)
        m = self.ref(o.first("MemoryRef", -1))
        if not isinstance(m, Mem) or cnt <= 0:
            return []
        sz = struct.calcsize("<" + fmt)
        n = min(cnt, len(m.data) // sz)
        out = list(struct.iter_unpack("<" + fmt, m.data[:n * sz]))
        return [r[0] if len(r) == 1 else r for r in out]
