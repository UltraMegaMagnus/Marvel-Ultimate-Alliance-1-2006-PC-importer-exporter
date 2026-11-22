"""Object-graph builder for writing MUA (v8) .igb files from the merged class template."""
import copy
import json
import os
import struct

import numpy as np

from .igb import IGB, Obj, Mem, MetaObject, _FIXED
from .igb_write import write_igb, NewMem

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


class RefMem(NewMem):
    """Memory block holding object references (igNodeList & co.); resolved when written."""
    __slots__ = ("refs",)

    def __init__(self, refs, tag, align, refc=1):
        NewMem.__init__(self, b"", tag, align, refc)
        self.refs = list(refs)

    @property
    def data(self):
        return struct.pack("<%di" % len(self.refs), *[(-1 if r is None else r.index) for r in self.refs])

    @data.setter
    def data(self, v):
        pass


class Template:
    _inst = None

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = Template()
        return cls._inst

    def __init__(self):
        self.g = IGB(os.path.join(_DIR, "mua_template.igb"))
        self.protos = {o.type: o for o in self.g.objects if isinstance(o, Obj)}
        self.meta = {m.name: m for m in self.g.metaobjs}
        with open(os.path.join(_DIR, "mua_roles.json")) as f:
            self.roles = {tuple([k.split("|")[0], int(k.split("|")[1])]): tuple(v) for k, v in json.load(f)["roles"].items()}
        self.kind_of = [m.short for m in self.g.metafields]
        with open(os.path.join(_DIR, "extra_schema.json")) as f:
            for name, spec in json.load(f).items():
                if name in self.meta:
                    continue
                parent = self.meta[spec["parent"]]
                fields = [(self.kind_of.index(k), slot, size) for k,slot,size in spec["fields"]]
                mo = MetaObject(len(self.g.metaobjs), name, fields, parent.index, spec.get("major", 1), spec.get("minor", 0), sc=spec.get("sc", max(x[1] for x in fields)+1))
                mo.parent = parent
                self.g.metaobjs.append(mo)
                self.meta[name] = mo
        from .igb_write import pack_meta, meta_tuples
        self.g.raw_mf, self.g.raw_mo = pack_meta(*meta_tuples(self.g))

    def role(self, cls, slot):
        r = self.roles.get((cls, slot))
        if r is None:                       # numeric/object list subclasses share their parent's role
            m = self.meta[cls].parent
            while m is not None and r is None:
                r = self.roles.get((m.name, slot))
                m = m.parent
        return r or (1, -1, 1)


def _default(kind, fsz):
    if kind in ("ObjectRef", "MemoryRef", "RawRef"):
        return None
    if kind == "String":
        return ""
    if kind in _FIXED:
        fmt = _FIXED[kind]
        n = struct.calcsize("<" + fmt)
        return 0 if len(struct.unpack("<" + fmt, bytes(n))) == 1 else tuple(struct.unpack("<" + fmt, bytes(n)))
    return bytes(fsz)


class Builder:
    def __init__(self, tpl=None):
        self.t = tpl or Template.get()
        self.items = []

    def add(self, it):
        self.items.append(it)
        return it

    # -- objects
    def obj(self, cls, fields=None):
        t = self.t
        meta = t.meta[cls]
        proto = t.protos.get(cls)
        o = Obj(-1, meta)
        for (ti, slot, fsz) in meta.fields:
            kind = t.kind_of[ti]
            if proto is not None and slot in proto.fields:
                val = copy.copy(proto.fields[slot])
            else:
                val = _default(kind, fsz)
            o.fields[slot], o.kinds[slot] = val, kind
        if fields:
            o.fields.update(fields)
        o.nf, o.tail = None, b""
        return self.add(o)

    def mem(self, cls, slot, data):
        tag, align, refc = self.t.role(cls, slot)
        return self.add(NewMem(data, tag, align, refc))

    def objlist(self, cls, items, extra=None):
        items = list(items)
        f = {2: len(items), 3: len(items)}
        if extra:
            f.update(extra)
        lst = self.obj(cls, f)
        if items:
            tag, align, refc = self.t.role(cls, 4)
            lst.fields[4] = self.add(RefMem(items, tag, align, refc))
        return lst

    def numlist(self, cls, values, fmt):
        """igIntList / igFloatList / igVec3fList ... ; values: numpy array or list; fmt: numpy dtype str."""
        arr = np.ascontiguousarray(values, dtype=fmt)
        count = int(arr.shape[0]) if arr.ndim > 1 else int(arr.size)
        if cls in ("igFloatList", "igIntList", "igUnsignedCharList", "igLongList", "igUnsignedIntList"):
            count = int(arr.size)
        lst = self.obj(cls, {2: count, 3: count})
        if count:
            lst.fields[4] = self.mem(cls, 4, arr.tobytes())
        return lst

    def set_kind(self, o, kind, value, nth=0):
        """Set the nth field (by slot order) of the given kind."""
        slots = [s for s in sorted(o.fields) if o.kinds[s] == kind]
        o.fields[slots[nth]] = value

    def finish(self, path, info):
        write_igb(path, self.t.g, self.items, info, names=self.t.g.names_raw)
