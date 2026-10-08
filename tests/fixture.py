"""Build a synthetic 'older iPhone' HEIC (no style data) for tests."""
from __future__ import annotations

import struct
from io import BytesIO


def apple_exif() -> bytes:
    """Exif item payload: Make/Model/Software + ExifIFD with an Apple MakerNote holding two tags."""
    be = ">"
    ent = lambda tag, typ, cnt, val: struct.pack(be + "HHI", tag, typ, cnt) + val
    mn_hdr = b"Apple iOS\0\0\x01MM"
    ifd_end = 14 + 2 + 2 * 12 + 4
    mn = bytearray(mn_hdr) + struct.pack(be + "H", 2)
    mn += ent(0x0001, 9, 1, struct.pack(be + "i", 7)) + ent(0x0008, 10, 3, struct.pack(be + "I", ifd_end))
    mn += struct.pack(be + "I", 0) + struct.pack(be + "6i", 1, 2, 3, 4, 5, 6)
    strings = [b"Apple\0", b"iPhone 15\0", b"17.0\0"]            # Make, Model, Software (out of line)
    ifd0_at, ifd0_len = 8, 2 + 4 * 12 + 4
    str_at = ifd0_at + ifd0_len
    offs, cur = [], str_at
    for x in strings:
        offs.append(cur)
        cur += len(x)
    exif_at = cur + (cur % 2)
    mn_at = exif_at + 2 + 12 + 4
    t = bytearray(b"MM\0*" + struct.pack(be + "I", ifd0_at))
    t += struct.pack(be + "H", 4)
    t += ent(0x010F, 2, len(strings[0]), struct.pack(be + "I", offs[0]))
    t += ent(0x0110, 2, len(strings[1]), struct.pack(be + "I", offs[1]))
    t += ent(0x0131, 2, len(strings[2]), struct.pack(be + "I", offs[2]))
    t += ent(0x8769, 4, 1, struct.pack(be + "I", exif_at)) + struct.pack(be + "I", 0)
    for x in strings:
        t += x
    t += b"\0" * (exif_at - len(t))
    t += struct.pack(be + "H", 1) + ent(0x927C, 7, len(mn), struct.pack(be + "I", mn_at)) + struct.pack(be + "I", 0)
    assert len(t) == mn_at, (len(t), mn_at)
    t += mn
    return struct.pack(">I", 6) + b"Exif\0\0" + bytes(t)


def make_heic(w=4032, h=3024) -> bytes:
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()
    img = Image.new("RGB", (w, h), (90, 140, 200))
    buf = BytesIO()
    img.save(buf, format="HEIF", quality=20, exif=apple_exif()[4:], thumbnails=[320])
    return buf.getvalue()
