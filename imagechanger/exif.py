"""Insert Apple MakerNote tag 0x54 (the Photographic Styles record) into an Exif item.

HEIF Exif item = 4-byte TIFF-header offset + (optional "Exif\\0\\0") + TIFF stream.
Apple's MakerNote is "Apple iOS\\0" + 2-byte version + "MM" + a big-endian IFD whose
out-of-line values are addressed relative to the start of the MakerNote.
"""
from __future__ import annotations

import plistlib
import struct

TAG_EXIF_IFD = 0x8769
TAG_MAKERNOTE = 0x927C
TAG_STYLE = 0x54
APPLE_HEADER = b"Apple iOS\0"
TYPE_UNDEFINED = 7
_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


class ExifError(RuntimeError):
    pass


def style_record() -> bytes:
    """The 8-member record every untouched native capture carries (neutral pad position)."""
    record = {"0": 1, "1": 0.0, "2": 0.0, "3": 1.0, "4": 1, "5": 1, "6": 4, "7": 0}
    return plistlib.dumps(record, fmt=plistlib.FMT_BINARY, sort_keys=False)


def _ifd_entries(t: bytearray, ifd: int, e: str):
    n = struct.unpack_from(e + "H", t, ifd)[0]
    return [(ifd + 2 + 12 * i) for i in range(n)]


def _find(t: bytearray, ifd: int, e: str, tag: int):
    for pos in _ifd_entries(t, ifd, e):
        if struct.unpack_from(e + "H", t, pos)[0] == tag:
            return pos
    return None


def add_style_tag(exif_item: bytes, record: bytes | None = None) -> bytes:
    record = record or style_record()
    tiff_off = 4 + struct.unpack_from(">I", exif_item, 0)[0]
    head, t = exif_item[:tiff_off], bytearray(exif_item[tiff_off:])
    if t[:2] == b"II":
        e = "<"
    elif t[:2] == b"MM":
        e = ">"
    else:
        raise ExifError("no TIFF header in Exif item")
    ifd0 = struct.unpack_from(e + "I", t, 4)[0]
    p = _find(t, ifd0, e, TAG_EXIF_IFD)
    if p is None:
        raise ExifError("no ExifIFD")
    exif_ifd = struct.unpack_from(e + "I", t, p + 8)[0]
    mp = _find(t, exif_ifd, e, TAG_MAKERNOTE)
    if mp is None:
        raise ExifError("no MakerNote")
    mn_len, mn_off = struct.unpack_from(e + "II", t, mp + 4)
    mn = bytes(t[mn_off:mn_off + mn_len])
    if not mn.startswith(APPLE_HEADER):
        raise ExifError("MakerNote is not Apple's")
    hdr_len = 14                              # "Apple iOS\0" + version(2) + byte order(2)
    me = ">" if mn[12:14] == b"MM" else "<"

    count = struct.unpack_from(me + "H", mn, hdr_len)[0]
    entries = []
    for i in range(count):
        o = hdr_len + 2 + 12 * i
        tag, typ, cnt = struct.unpack_from(me + "HHI", mn, o)
        entries.append([tag, typ, cnt, mn[o + 8:o + 12]])
    old_end = hdr_len + 2 + 12 * count + 4    # entries + next-IFD pointer
    nxt = mn[old_end - 4:old_end]
    entries = [x for x in entries if x[0] != TAG_STYLE]
    removed = count - len(entries)

    # Layout: header | count | entries(+1) | next | old out-of-line area | new record
    grow = 12 * (1 + 0) - 12 * removed        # net new entries * 12
    old_tail = mn[old_end:]
    shifted = []
    for tag, typ, cnt, val in entries:
        size = _SIZES.get(typ, 1) * cnt
        if size > 4:
            off = struct.unpack(me + "I", val)[0]
            val = struct.pack(me + "I", off + grow)
        shifted.append([tag, typ, cnt, val])
    # the record is appended after the old tail (dropped old record bytes remain unused)
    rec_off = old_end + grow + len(old_tail)
    shifted.append([TAG_STYLE, TYPE_UNDEFINED, len(record), struct.pack(me + "I", rec_off)])
    shifted.sort(key=lambda x: x[0])
    new_mn = bytearray(mn[:hdr_len]) + struct.pack(me + "H", len(shifted))
    for tag, typ, cnt, val in shifted:
        new_mn += struct.pack(me + "HHI", tag, typ, cnt) + val
    new_mn += nxt + old_tail + record
    if len(new_mn) % 2:
        new_mn += b"\0"

    # Append the rebuilt MakerNote to the TIFF stream and repoint 0x927C; the old copy
    # stays where it is, so no other Exif offset moves.
    if len(t) % 2:
        t += b"\0"
    new_off = len(t)
    t += new_mn
    struct.pack_into(e + "II", t, mp + 4, len(new_mn), new_off)
    return bytes(head) + bytes(t)


DEVICE = {0x010F: "Apple", 0x0110: "iPhone 18 Pro", 0x0131: "27.0"}   # Make, Model, Software


def set_device(exif_item: bytes, values: dict | None = None) -> bytes:
    """Overwrite the ASCII Make/Model/Software tags of IFD0 (tags that are absent stay absent)."""
    values = DEVICE if values is None else values
    tiff_off = 4 + struct.unpack_from(">I", exif_item, 0)[0]
    head, t = exif_item[:tiff_off], bytearray(exif_item[tiff_off:])
    e = "<" if t[:2] == b"II" else ">"
    ifd0 = struct.unpack_from(e + "I", t, 4)[0]
    for tag, text in values.items():
        pos = _find(t, ifd0, e, tag)
        if pos is None:
            continue
        raw = text.encode("ascii") + b"\0"
        if len(raw) <= 4:
            struct.pack_into(e + "HHI", t, pos, tag, 2, len(raw))
            t[pos + 8:pos + 12] = raw.ljust(4, b"\0")
        else:
            if len(t) % 2:
                t += b"\0"
            off = len(t)
            t += raw
            struct.pack_into(e + "HHII", t, pos, tag, 2, len(raw), off)
    return bytes(head) + bytes(t)
