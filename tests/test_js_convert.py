"""JPEG -> HEIC -> styled HEIC in JS, with the HEVC encoder replaced by ffmpeg-made stills."""
import json
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
from imagechanger.styles import URI_STYLES             # noqa: E402
from tests import jpeg_case                            # noqa: E402


@unittest.skipUnless(shutil.which("node") and shutil.which("ffmpeg"), "needs node and ffmpeg")
class ConvertTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        for role, (w, h) in {"main": (4032, 3024), "thumb": (1024, 768)}.items():
            hvcc, sample = jpeg_case.encode_still(w, h)
            (cls.dir / f"{role}.hvcc").write_bytes(hvcc)
            (cls.dir / f"{role}.sample").write_bytes(sample)

    def run_js(self, jpeg):
        a, b = self.dir / "in.jpg", self.dir / "out.heic"
        a.write_bytes(jpeg)
        r = subprocess.run(["node", str(ROOT / "tests" / "js_convert.mjs"), str(a), str(b), str(self.dir)],
                           check=True, capture_output=True, text=True)
        return b.read_bytes(), json.loads(r.stdout)

    def check(self, out):
        import pillow_heif
        from PIL import Image
        pillow_heif.register_heif_opener()
        img = Image.open(BytesIO(out)).convert("RGB")
        self.assertEqual(img.size, (4032, 3024))
        px = img.getpixel((2000, 1500))
        self.assertTrue(all(abs(a - b) < 14 for a, b in zip(px, jpeg_case.COLOR)), px)
        m, _ = parse_meta(out)
        self.assertTrue(any(i.content_type == URI_STYLES for i in m.items.values()))
        self.assertIn(b"heix", out[:64])

    def test_jpeg_with_apple_exif_keeps_it(self):
        out, info = self.run_js(jpeg_case.make_jpeg())
        self.assertFalse(info["synthesized"])
        self.check(out)
        m, _ = parse_meta(out)
        exif = item_bytes(out, m, m.by_type(b"Exif")[0])
        self.assertIn(b"\x00\x01\x00\t\x00\x00\x00\x01\x00\x00\x00\x07", exif)   # original MakerNote tag 0x0001 = 7

    def test_jpeg_without_exif_gets_synthetic_makernote(self):
        out, info = self.run_js(jpeg_case.make_jpeg(exif=False))
        self.assertTrue(info["synthesized"])
        self.check(out)


if __name__ == "__main__":
    unittest.main()
