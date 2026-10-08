import struct
import sys
import unittest
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imagechanger import exif, styles                              # noqa: E402
from imagechanger.bmff import item_bytes, parse_meta                # noqa: E402
from imagechanger.patch import AlreadyStyled, patch                # noqa: E402
from tests import fixture                                          # noqa: E402


class PatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = fixture.make_heic()
        cls.res = patch(cls.src)
        cls.out = cls.res.data

    def test_original_items_untouched(self):
        a, _ = parse_meta(self.src)
        b, _ = parse_meta(self.out)
        for iid, it in a.items.items():
            if it.item_type == b"Exif":
                continue
            self.assertEqual(item_bytes(self.src, a, it), item_bytes(self.out, b, b.items[iid]))
            self.assertEqual(it.props, b.items[iid].props)

    def test_pixels_identical(self):
        import pillow_heif
        from PIL import Image
        pillow_heif.register_heif_opener()
        x, y = Image.open(BytesIO(self.src)), Image.open(BytesIO(self.out))
        self.assertEqual(x.size, y.size)
        self.assertEqual(x.tobytes(), y.tobytes())

    def test_style_items_present(self):
        m, _ = parse_meta(self.out)
        uris = {m.aux_uri(i) for i in m.items.values()}
        self.assertIn(styles.URI_LINEAR_THUMB, uris)
        self.assertIn(styles.URI_STYLE_DELTA, uris)
        sid = self.res.report["styles_item"]
        self.assertEqual(m.items[sid].content_type, styles.URI_STYLES)
        rows, cols = self.res.report["tiles"]
        grid = m.items[self.res.report["grid_item"]]
        dimg = [ts for rt, f, ts in m.refs if rt == b"dimg" and f == grid.item_id][0]
        self.assertEqual(len(dimg), rows * cols)
        self.assertEqual(item_bytes(self.out, m, grid)[:4], bytes([0, 0, rows - 1, cols - 1]))
        for t in dimg:
            self.assertEqual(m.items[t].item_type, b"hvc1")

    def test_extents_inside_file(self):
        m, _ = parse_meta(self.out)
        for it in m.items.values():
            if it.loc[0] == "file":
                self.assertLessEqual(it.loc[1] + it.loc[2], len(self.out), it)

    def test_makernote_tag(self):
        m, _ = parse_meta(self.out)
        data = item_bytes(self.out, m, m.by_type(b"Exif")[0])
        self.assertIn(exif.style_record(), data)
        # original tags survive and out-of-line values still resolve
        t = data[4 + struct.unpack(">I", data[:4])[0]:]
        mp = t.index(struct.pack(">HH", 0x927C, 7)) + 4
        n, off = struct.unpack(">II", t[mp:mp + 8])
        mn = t[off:off + n]
        cnt = struct.unpack(">H", mn[14:16])[0]
        tags = {}
        for i in range(cnt):
            tag, typ, c, v = struct.unpack(">HHII", mn[16 + 12 * i:28 + 12 * i])
            tags[tag] = (typ, c, v)
        self.assertEqual(sorted(tags), [0x01, 0x08, 0x54])
        self.assertEqual(tags[0x01][2], 7)
        voff = tags[0x08][2]
        self.assertEqual(struct.unpack(">6i", mn[voff:voff + 24]), (1, 2, 3, 4, 5, 6))
        o54 = tags[0x54][2]
        self.assertEqual(mn[o54:o54 + tags[0x54][1]], exif.style_record())

    def test_ftyp_brands(self):
        self.assertIn(b"heix", self.out[:64])

    def test_idempotence_guard(self):
        with self.assertRaises(AlreadyStyled):
            patch(self.out)


if __name__ == "__main__":
    unittest.main()
