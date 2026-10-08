"""Build a synthetic 'older iPhone' HEIC (no style data) for tests."""
from __future__ import annotations

import struct
from io import BytesIO


def apple_exif() -> bytes:
    """Exif item payload: Make/Model + ExifIFD with an Apple MakerNote holding two tags."""
    be = ">"
    # MakerNote: header(14) + IFD(2 entries) ; value of tag 0x0001 (type 9 slong) inline,
    # tag 0x0008 (type 10, 24 bytes) out-of-line at MN offset after the IFD.
    mn_hdr = b"Apple iOS\0\0\x01MM"
    ifd_end = 14 + 2 + 2 * 12 + 4
    mn = bytearray(mn_hdr)
    mn += struct.pack(be + "H", 2)
    mn += struct.pack(be + "HHII", 0x0001, 9, 1, 7)
    mn += struct.pack(be + "HHII", 0x0008, 10, 3, ifd_end)
    mn += struct.pack(be + "I", 0)
    mn += struct.pack(be + "6i", 1, 2, 3, 4, 5, 6)
    # TIFF: header, IFD0 (1 entry: ExifIFD ptr), ExifIFD (1 entry: MakerNote)
    t = bytearray(b"MM\0*" + struct.pack(be + "I", 8))
    t += struct.pack(be + "H", 1) + struct.pack(be + "HHII", 0x8769, 4, 1, 26) + struct.pack(be + "I", 0)
    t += struct.pack(be + "H", 1) + struct.pack(be + "HHII", 0x927C, 7, len(mn), 44) + struct.pack(be + "I", 0)
    assert len(t) == 44, len(t)
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
