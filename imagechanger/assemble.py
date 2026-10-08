"""Write an edited HEIF file: new ftyp/meta in front, original payloads kept in place."""
from __future__ import annotations

from .bmff import Meta, box, iter_boxes, serialise_meta


def assemble(buf: bytes, meta: Meta, new_ftyp: bytes) -> bytes:
    boxes_ = list(iter_boxes(buf))
    old_ftyp = next((b for b in boxes_ if b[0] == b"ftyp"), None)
    old_meta = next((b for b in boxes_ if b[0] == b"meta"), None)
    if old_ftyp is None or old_meta is None:
        raise ValueError("file lacks ftyp or meta")
    d_ftyp = len(new_ftyp) - (old_ftyp[3] - old_ftyp[1])

    def shift_with(d_meta: int):
        def shift(off: int) -> int:
            out = off
            if off >= old_ftyp[3]:
                out += d_ftyp
            if off >= old_meta[3]:
                out += d_meta
            return out
        return shift

    new_payloads = [(i.item_id, i.loc[1]) for i in meta.items.values() if i.loc[0] == "new"]

    # The meta size does not depend on offset values (fixed-width fields), so build twice.
    probe = serialise_meta(meta, {}, shift_with(0))
    d_meta = len(probe) - (old_meta[3] - old_meta[1])
    shift = shift_with(d_meta)

    head = b""
    tail_start = 0
    for kind, b0, _p0, b1 in boxes_:
        if kind == b"ftyp":
            head += new_ftyp
        elif kind == b"meta":
            head += b"\0" * len(probe)   # placeholder, replaced below
        else:
            head += buf[b0:b1]
    mdat_payload_start = len(head) + 8
    offsets, cursor = {}, mdat_payload_start
    blob = b""
    for iid, data in new_payloads:
        offsets[iid] = cursor
        blob += data
        cursor += len(data)
    final_meta = serialise_meta(meta, offsets, shift)
    assert len(final_meta) == len(probe)

    out = b""
    for kind, b0, _p0, b1 in boxes_:
        if kind == b"ftyp":
            out += new_ftyp
        elif kind == b"meta":
            out += final_meta
        else:
            out += buf[b0:b1]
    return out + (box(b"mdat", blob) if blob else b"")
