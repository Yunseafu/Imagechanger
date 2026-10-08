import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imagechanger.server import Handler       # noqa: E402
from tests import fixture                      # noqa: E402


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_port}/patch"
        cls.src = fixture.make_heic()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def post(self, body, headers=None):
        req = urllib.request.Request(self.url, data=body, headers=headers or {}, method="POST")
        try:
            return urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            return e

    def test_raw_body(self):
        r = self.post(self.src)
        self.assertEqual(r.status, 200)
        out = r.read()
        self.assertGreater(len(out), len(self.src))
        self.assertEqual(self.post(out).status, 409)

    def test_multipart(self):
        b = b"----x"
        body = (b"--" + b + b'\r\nContent-Disposition: form-data; name="photo"; filename="a.HEIC"\r\n'
                b"Content-Type: image/heic\r\n\r\n" + self.src + b"\r\n--" + b + b"--\r\n")
        r = self.post(body, {"Content-Type": "multipart/form-data; boundary=" + b.decode()})
        self.assertEqual(r.status, 200)

    def test_not_heic(self):
        self.assertEqual(self.post(b"\xff\xd8\xff\xe0" + b"0" * 100).status, 415)


if __name__ == "__main__":
    unittest.main()
