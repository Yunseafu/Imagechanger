"""Tiny LAN-only HTTP service so an iOS Shortcut can send a photo and get the patched one back.

POST /patch  body = raw HEIC bytes, or multipart/form-data with the file in any field.
  200 image/heic   patched photo
  409 text         already has style data
  415 text         not a HEIC
  422 text         HEIC that cannot be patched (reason in body)
GET  /           status page with the Shortcut recipe
"""
from __future__ import annotations

import ipaddress
import socket
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .bmff import HeifError
from .cli import looks_like_heic
from .patch import AlreadyStyled, patch

MAX_BYTES = 200 * 1024 * 1024


def lan_addresses() -> list[str]:
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))     # no packet is sent; picks the LAN interface
        found.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127."))


def is_local_client(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return ip.is_private or ip.is_loopback or ip.is_link_local


def extract_body(headers, raw: bytes) -> bytes:
    ctype = headers.get("Content-Type", "")
    if not ctype.startswith("multipart/"):
        return raw
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
    for part in msg.iter_parts():
        payload = part.get_payload(decode=True)
        if payload and looks_like_heic(payload):
            return payload
    parts = [p.get_payload(decode=True) for p in msg.iter_parts()]
    return next((p for p in parts if p), b"")


class Handler(BaseHTTPRequestHandler):
    server_version = f"imagechanger/{__version__}"

    def _send(self, code, body: bytes, ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _guard(self) -> bool:
        if not is_local_client(self.client_address[0]):
            self._send(403, b"local network only")
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        if self.path == "/api/status":
            self._send(200, b'{"ok": true}', "application/json")
            return
        addrs = ", ".join(f"http://{a}:{self.server.server_port}/patch" for a in lan_addresses()) or "(none found)"
        page = (f"<h1>imagechanger {__version__}</h1><p>Shortcut URL(s): <code>{addrs}</code></p>"
                "<p>Use only on your home network; this server has no login.</p>")
        self._send(200, page.encode(), "text/html; charset=utf-8")

    def do_POST(self):
        if not self._guard() or self.path.split("?")[0] != "/patch":
            if self.path.split("?")[0] != "/patch":
                self._send(404, b"not found")
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BYTES:
            self._send(413, b"empty or too large")
            return
        data = extract_body(self.headers, self.rfile.read(n))
        if not looks_like_heic(data):
            self._send(415, b"Not a HEIC photo. Send the original (Settings > Camera > Formats > High Efficiency).")
            return
        try:
            res = patch(data)
        except AlreadyStyled as e:
            self._send(409, f"already: {e}".encode())
        except HeifError as e:
            self._send(422, str(e).encode())
        else:
            self._send(200, res.data, "image/heic", {"X-Imagechanger": __version__})


def serve(host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"imagechanger {__version__} listening on {host}:{port}")
    for a in lan_addresses():
        print(f"  Shortcut URL: http://{a}:{port}/patch")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
