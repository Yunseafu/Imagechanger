"""The JS patcher (web/patcher.js) must produce the same item graph as the Python one."""
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from imagechanger.bmff import item_bytes, parse_meta   # noqa: E402
from imagechanger.patch import patch                   # noqa: E402
from tests import fixture                              # noqa: E402


def makernote_tags(data):
    import struct
    t = data[4 + struct.unpack(">I", data[:4])[0]:]
    mp = t.index(struct.pack(">HH", 0x927C, 7)) + 4
    n, off = struct.unpack(">II", t[mp:mp + 8])
    mn = t[off:off + n]
    out = {}
    for i in range(struct.unpack(">H", mn[14:16])[0]):
        tag, typ, c, v = struct.unpack(">HHII", mn[16 + 12 * i:28 + 12 * i])
        size = {7: 1, 9: 4, 10: 8}[typ] * c
        raw = mn[v:v + size] if size > 4 else mn[16 + 12 * i + 8:16 + 12 * i + 8 + size]
        out[tag] = plistlib.loads(raw) if tag == 0x54 else raw
    return out


@unittest.skipUnless(shutil.which("node"), "node not installed")
class JsParity(unittest.TestCase):
    def test_same_graph(self):
        src = fixture.make_heic()
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "in.heic"), Path(d, "out.heic")
            a.write_bytes(src)
            subprocess.run(["node", str(ROOT / "tests" / "js_parity.mjs"), str(a), str(b)], check=True)
            js = b.read_bytes()
        py = patch(src).data
        mj, _ = parse_meta(js)
        mp, _ = parse_meta(py)
        self.assertEqual(list(mj.items), list(mp.items))
        for iid in mp.items:
            x, y = mj.items[iid], mp.items[iid]
            self.assertEqual((x.item_type, x.name, x.content_type, x.hidden, x.props),
                             (y.item_type, y.name, y.content_type, y.hidden, y.props), iid)
            bj, bp = item_bytes(js, mj, x), item_bytes(py, mp, y)
            if x.item_type == b"Exif":
                self.assertEqual(makernote_tags(bj), makernote_tags(bp))
                self.assertIn(b"iPhone 18 Pro\0", bj)
                self.assertIn(b"iPhone 18 Pro\0", bp)
                continue
            if x.item_type == b"uri ":
                self.assertEqual(plistlib.loads(bj), plistlib.loads(bp))
            else:
                self.assertEqual(bj, bp, f"item {iid} payload differs")
        self.assertEqual(sorted(mj.refs), sorted(mp.refs))
        self.assertEqual(mj.properties, mp.properties)
        import pillow_heif
        from PIL import Image
        pillow_heif.register_heif_opener()
        self.assertEqual(Image.open(BytesIO(js)).tobytes(), Image.open(BytesIO(src)).tobytes())


if __name__ == "__main__":
    unittest.main()
