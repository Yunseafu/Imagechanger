"""Minimal ISO-BMFF (HEIF container) reader and writer.

Only what the patcher needs: a box iterator, FullBox helpers and a model of the
``meta`` box (items, locations, references, properties) that can be edited and
serialised again.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


class HeifError(RuntimeError):
    pass


def box(kind: bytes, payload: bytes) -> bytes:
    if len(kind) != 4:
        raise ValueError("box type must be 4 bytes")
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def full_box(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return box(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def iter_boxes(buf: bytes, start: int = 0, end: int | None = None):
    """Yield ``(type, box_start, payload_start, box_end)`` for each box in a range."""
    end = len(buf) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", buf, pos)
        head = 8
        if size == 1:
            size = struct.unpack_from(">Q", buf, pos + 8)[0]
            head = 16
        elif size == 0:
            size = end - pos
        if size < head or pos + size > end:
            raise HeifError(f"corrupt box {kind!r} at {pos}")
        yield kind, pos, pos + head, pos + size
        pos += size


def find_box(buf: bytes, kind: bytes, start: int = 0, end: int | None = None):
    for k, b0, p0, b1 in iter_boxes(buf, start, end):
        if k == kind:
            return b0, p0, b1
    return None


# --------------------------------------------------------------------------- items

@dataclass
class Item:
    item_id: int
    item_type: bytes            # b"hvc1", b"grid", b"Exif", b"uri ", ...
    name: str = ""
    content_type: str = ""      # MIME type, or URI for "uri " items
    hidden: bool = False
    # Where the bytes live: ("file", offset, length) or ("idat", offset, length)
    # or ("new", payload) for data added by the patcher.
    loc: tuple = ("none",)
    props: list = field(default_factory=list)   # [(1-based property index, essential)]


@dataclass
class Meta:
    """Editable view of the ``meta`` box of a HEIF file."""
    primary: int = 0
    items: dict = field(default_factory=dict)       # item_id -> Item (insertion ordered)
    refs: list = field(default_factory=list)        # [(ref_type, from_id, [to_ids])]
    properties: list = field(default_factory=list)  # raw property boxes (ipco children)
    idat: bytes = b""
    other: list = field(default_factory=list)       # untouched children: (type, raw box bytes)

    # ---- queries
    def by_type(self, item_type: bytes):
        return [i for i in self.items.values() if i.item_type == item_type]

    def prop_box(self, item: Item, kind: bytes):
        for index, _ess in item.props:
            raw = self.properties[index - 1]
            if raw[4:8] == kind:
                return raw
        return None

    def prop_index(self, item: Item, kind: bytes):
        for index, _ess in item.props:
            if self.properties[index - 1][4:8] == kind:
                return index
        return None

    def refs_from(self, ref_type: bytes, item_id: int):
        return [t for rt, f, ts in self.refs if rt == ref_type and f == item_id for t in ts]

    def aux_uri(self, item: Item):
        raw = self.prop_box(item, b"auxC")
        if raw is None:
            return None
        return raw[12:].split(b"\0", 1)[0].decode("ascii", "replace")

    # ---- edits
    def add_property(self, raw: bytes) -> int:
        self.properties.append(raw)
        return len(self.properties)

    def next_item_id(self) -> int:
        return max(self.items, default=0) + 1

    def add_item(self, item_type: bytes, payload: bytes | None = None, *, name: str = "",
                 content_type: str = "", hidden: bool = True, props=(), ref_type=None,
                 ref_to=(), in_idat: bool = False) -> int:
        iid = self.next_item_id()
        item = Item(iid, item_type, name, content_type, hidden, props=list(props))
        if payload is not None:
            if in_idat:
                item.loc = ("idat", len(self.idat), len(payload))
                self.idat += payload
            else:
                item.loc = ("new", payload)
        self.items[iid] = item
        if ref_type and ref_to:
            self.refs.append((ref_type, iid, list(ref_to)))
        return iid


# ------------------------------------------------------------------------ parsing

def _uint(buf, pos, size):
    return int.from_bytes(buf[pos:pos + size], "big") if size else 0


def parse_meta(buf: bytes) -> tuple[Meta, tuple[int, int]]:
    """Parse the top-level ``meta`` box. Returns the model and (box_start, box_end)."""
    found = find_box(buf, b"meta")
    if not found:
        raise HeifError("no meta box: not a HEIF file")
    b0, p0, b1 = found
    meta = Meta()
    ipma_rows = []
    infe_rows = {}
    iloc_rows = {}
    p = p0 + 4  # FullBox header of meta
    for kind, c0, cp, c1 in iter_boxes(buf, p, b1):
        raw = buf[c0:c1]
        if kind == b"pitm":
            ver = buf[cp]
            meta.primary = _uint(buf, cp + 4, 2 if ver == 0 else 4)
        elif kind == b"iinf":
            ver = buf[cp]
            q = cp + 4 + (2 if ver == 0 else 4)
            for k2, e0, ep, e1 in iter_boxes(buf, q, c1):
                if k2 != b"infe":
                    continue
                v = buf[ep]
                if v < 2:
                    raise HeifError("infe version < 2 not supported")
                flags = _uint(buf, ep + 1, 3)
                idsz = 2 if v == 2 else 4
                iid = _uint(buf, ep + 4, idsz)
                itype = buf[ep + 4 + idsz + 2: ep + 8 + idsz + 2]
                rest = buf[ep + 8 + idsz + 2: e1].split(b"\0")
                name = rest[0].decode("utf-8", "replace") if rest else ""
                ctype = rest[1].decode("utf-8", "replace") if len(rest) > 1 else ""
                infe_rows[iid] = Item(iid, itype, name, ctype, bool(flags & 1))
        elif kind == b"iloc":
            iloc_rows = _parse_iloc(buf, cp, c1)
        elif kind == b"iref":
            ver = buf[cp]
            sz = 2 if ver == 0 else 4
            for rt, r0, rp, r1 in iter_boxes(buf, cp + 4, c1):
                frm = _uint(buf, rp, sz)
                n = _uint(buf, rp + sz, 2)
                tos = [_uint(buf, rp + sz + 2 + i * sz, sz) for i in range(n)]
                meta.refs.append((rt, frm, tos))
        elif kind == b"iprp":
            for k2, e0, ep, e1 in iter_boxes(buf, cp, c1):
                if k2 == b"ipco":
                    meta.properties = [buf[a:d] for _k, a, _p, d in iter_boxes(buf, ep, e1)]
                elif k2 == b"ipma":
                    ipma_rows = _parse_ipma(buf, ep, e1)
        elif kind == b"idat":
            meta.idat = buf[cp:c1]
        else:
            meta.other.append((kind, raw))
    for iid, assoc in ipma_rows:
        if iid in infe_rows:
            infe_rows[iid].props = assoc
    for iid, loc in iloc_rows.items():
        if iid in infe_rows:
            infe_rows[iid].loc = loc
    meta.items = infe_rows
    return meta, (b0, b1)


def _parse_iloc(buf, cp, end):
    rows = {}
    ver = buf[cp]
    a, b = buf[cp + 4], buf[cp + 5]
    off_sz, len_sz, base_sz, idx_sz = a >> 4, a & 15, b >> 4, (b & 15) if ver in (1, 2) else 0
    p = cp + 6
    count = _uint(buf, p, 2 if ver < 2 else 4)
    p += 2 if ver < 2 else 4
    for _ in range(count):
        iid = _uint(buf, p, 2 if ver < 2 else 4)
        p += 2 if ver < 2 else 4
        method = 0
        if ver in (1, 2):
            method = _uint(buf, p, 2) & 15
            p += 2
        p += 2  # data_reference_index
        base = _uint(buf, p, base_sz)
        p += base_sz
        n_ext = _uint(buf, p, 2)
        p += 2
        extents = []
        for _e in range(n_ext):
            if ver in (1, 2) and idx_sz:
                p += idx_sz
            eo = _uint(buf, p, off_sz)
            p += off_sz
            el = _uint(buf, p, len_sz)
            p += len_sz
            extents.append((base + eo, el))
        if len(extents) != 1:
            raise HeifError(f"item {iid}: multi-extent items are not supported")
        rows[iid] = ("idat" if method == 1 else "file", extents[0][0], extents[0][1])
    return rows


def _parse_ipma(buf, ep, end):
    ver = buf[ep]
    flags = _uint(buf, ep + 1, 3)
    n = _uint(buf, ep + 4, 4)
    p = ep + 8
    rows = []
    for _ in range(n):
        iid = _uint(buf, p, 2 if ver == 0 else 4)
        p += 2 if ver == 0 else 4
        cnt = buf[p]
        p += 1
        assoc = []
        for _a in range(cnt):
            if flags & 1:
                v = _uint(buf, p, 2)
                p += 2
                assoc.append((v & 0x7FFF, bool(v & 0x8000)))
            else:
                v = buf[p]
                p += 1
                assoc.append((v & 0x7F, bool(v & 0x80)))
        rows.append((iid, assoc))
    return rows


def item_bytes(buf: bytes, meta: Meta, item: Item) -> bytes:
    kind = item.loc[0]
    if kind == "file":
        return buf[item.loc[1]:item.loc[1] + item.loc[2]]
    if kind == "idat":
        return meta.idat[item.loc[1]:item.loc[1] + item.loc[2]]
    if kind == "new":
        return item.loc[1]
    raise HeifError(f"item {item.item_id} has no data")


# ---------------------------------------------------------------------- serialising

def serialise_meta(meta: Meta, file_offset_for_new: dict, shift_file: callable) -> bytes:
    """Build a fresh ``meta`` box.

    ``file_offset_for_new``: item_id -> absolute offset of its payload in the appended mdat.
    ``shift_file``: maps an original absolute offset to its offset in the output.
    """
    big = max(meta.items, default=0) > 0xFFFF
    # iinf
    infes = b""
    for it in meta.items.values():
        ver = 3 if big else 2
        body = bytes([ver]) + (1 if it.hidden else 0).to_bytes(3, "big")
        body += it.item_id.to_bytes(4 if big else 2, "big") + b"\0\0" + it.item_type
        body += it.name.encode() + b"\0"
        if it.item_type in (b"mime", b"uri "):
            body += it.content_type.encode() + b"\0"
        infes += box(b"infe", body)
    iinf = full_box(b"iinf", 1 if big else 0, 0, len(meta.items).to_bytes(4 if big else 2, "big") + infes)

    # iloc: version 1, 4-byte offset/length, no base offset
    rows = b""
    for it in meta.items.values():
        if it.loc[0] == "none":
            continue
        method = 1 if it.loc[0] == "idat" else 0
        if it.loc[0] == "file":
            off = shift_file(it.loc[1])
        elif it.loc[0] == "new":
            off = file_offset_for_new.get(it.item_id, 0)
        else:
            off = it.loc[1]
        length = len(it.loc[1]) if it.loc[0] == "new" else it.loc[2]
        rows += (it.item_id.to_bytes(4 if big else 2, "big") + method.to_bytes(2, "big")
                 + b"\0\0" + b"\0\1" + off.to_bytes(4, "big") + length.to_bytes(4, "big"))
    n_loc = sum(1 for it in meta.items.values() if it.loc[0] != "none")
    iloc = full_box(b"iloc", 1, 0, bytes([0x44, 0x00]) + n_loc.to_bytes(2, "big") + rows)
    if big:  # version 2 for 32-bit item ids
        iloc = full_box(b"iloc", 2, 0, bytes([0x44, 0x00]) + n_loc.to_bytes(4, "big") + rows)

    # iref
    iref = b""
    if meta.refs:
        sz = 4 if big else 2
        body = b""
        for rt, frm, tos in meta.refs:
            body += box(rt, frm.to_bytes(sz, "big") + len(tos).to_bytes(2, "big")
                        + b"".join(t.to_bytes(sz, "big") for t in tos))
        iref = full_box(b"iref", 1 if big else 0, 0, body)

    # iprp = ipco + ipma
    ipco = box(b"ipco", b"".join(meta.properties))
    wide = len(meta.properties) > 127
    ipma_rows = [it for it in meta.items.values() if it.props]
    body = len(ipma_rows).to_bytes(4, "big")
    for it in ipma_rows:
        body += it.item_id.to_bytes(4 if big else 2, "big") + bytes([len(it.props)])
        for idx, ess in it.props:
            if wide:
                body += ((0x8000 if ess else 0) | idx).to_bytes(2, "big")
            else:
                body += bytes([(0x80 if ess else 0) | idx])
    ipma = full_box(b"ipma", 1 if big else 0, 1 if wide else 0, body)
    iprp = box(b"iprp", ipco + ipma)

    pitm = full_box(b"pitm", 0 if meta.primary <= 0xFFFF else 1, 0,
                    meta.primary.to_bytes(2 if meta.primary <= 0xFFFF else 4, "big"))
    idat = box(b"idat", meta.idat) if meta.idat else b""

    others = {k: raw for k, raw in meta.other}
    order = []
    for k, raw in meta.other:          # keep hdlr etc. first, as in the source
        if k in (b"hdlr",):
            order.append(raw)
    parts = order + [pitm, iinf, iref, iprp, iloc, idat]
    parts += [raw for k, raw in meta.other if k != b"hdlr"]
    return full_box(b"meta", 0, 0, b"".join(parts))
