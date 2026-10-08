"""Helpers for the JPEG->HEIC test: a JPEG fixture and pre-encoded HEVC stills (via ffmpeg)."""
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

from imagechanger.bmff import find_box
from tests import fixture

COLOR = (90, 140, 200)


def make_jpeg(w=4032, h=3024, exif=True, orientation=None) -> bytes:
    from PIL import Image
    buf = BytesIO()
    kw = {"exif": b"Exif\0\0" + fixture.apple_exif()[10:]} if exif else {}
    if orientation:
        ex = Image.Exif()
        ex[0x0112] = orientation
        kw = {"exif": ex.tobytes()}
    Image.new("RGB", (w, h), COLOR).save(buf, "JPEG", quality=90, **kw)
    return buf.getvalue()


def encode_still(w, h) -> tuple[bytes, bytes]:
    """(hvcC box, length-prefixed sample) of a flat 8-bit 4:2:0 HEVC still of the test colour."""
    hexcol = "0x%02x%02x%02x" % COLOR
    with tempfile.TemporaryDirectory() as d:
        mp4 = Path(d, "t.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c={hexcol}:s={w}x{h}:r=1,format=yuv420p", "-frames:v", "1", "-c:v", "libx265",
                        "-x265-params", "log-level=none:keyint=1:no-info=1", "-tag:v", "hvc1", "-f", "mp4", str(mp4)],
                       check=True)
        buf = mp4.read_bytes()
    moov = find_box(buf, b"moov")
    p = moov[1]
    for kind in (b"trak", b"mdia", b"minf", b"stbl", b"stsd"):
        _b0, p, e = find_box(buf, kind, p, moov[2])
    entry = p + 8
    esize = int.from_bytes(buf[entry:entry + 4], "big")
    hv0, _hp, hv1 = find_box(buf, b"hvcC", entry + 8 + 78, entry + esize)
    mdat = find_box(buf, b"mdat")
    sample, q, out = buf[mdat[1]:mdat[2]], 0, b""
    while q < len(sample):
        ln = int.from_bytes(sample[q:q + 4], "big")
        if ((sample[q + 4] >> 1) & 63) < 32:
            out += sample[q:q + 4 + ln]
        q += 4 + ln
    return buf[hv0:hv1], out
