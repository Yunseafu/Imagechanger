"""Generate a "Display P3 Linear" ICC profile (matrix/TRC, v4) for the delta-map ``colr`` box."""
from __future__ import annotations

import struct

P3_PRIMARIES = ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060))
D65 = (0.3127, 0.3290)
D50_XYZ = (0.9642, 1.0, 0.8249)


def _xyz(x, y):
    return (x / y, 1.0, (1 - x - y) / y)


def _inv3(m):
    a, b, c = m[0]; d, e, f = m[1]; g, h, i = m[2]
    det = a*(e*i - f*h) - b*(d*i - f*g) + c*(d*h - e*g)
    return [[(e*i - f*h)/det, (c*h - b*i)/det, (b*f - c*e)/det],
            [(f*g - d*i)/det, (a*i - c*g)/det, (c*d - a*f)/det],
            [(d*h - e*g)/det, (b*g - a*h)/det, (a*e - b*d)/det]]


def _mul(m, v):
    return [sum(m[r][c] * v[c] for c in range(3)) for r in range(3)]


def _matmul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)] for r in range(3)]


BRADFORD = [[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]]


def _adaptation(src_white, dst_white):
    s = _mul(BRADFORD, src_white)
    d = _mul(BRADFORD, dst_white)
    scale = [[d[0] / s[0], 0, 0], [0, d[1] / s[1], 0], [0, 0, d[2] / s[2]]]
    return _matmul(_inv3(BRADFORD), _matmul(scale, BRADFORD))


def _colorants():
    prim = [_xyz(*p) for p in P3_PRIMARIES]
    m = [[prim[c][r] for c in range(3)] for r in range(3)]
    white = _xyz(*D65)
    s = _mul(_inv3(m), white)
    rgb_to_xyz = [[m[r][c] * s[c] for c in range(3)] for r in range(3)]
    adapt = _adaptation(white, D50_XYZ)
    return _matmul(adapt, rgb_to_xyz), adapt


def _s15(v):
    return struct.pack(">i", round(v * 65536))


def _xyz_tag(v):
    return b"XYZ \0\0\0\0" + b"".join(_s15(c) for c in v)


def _mluc(text):
    s = text.encode("utf-16-be")
    return b"mluc\0\0\0\0" + struct.pack(">II", 1, 12) + b"enUS" + struct.pack(">II", len(s), 28) + s


def display_p3_linear() -> bytes:
    m, adapt = _colorants()
    tags = {
        b"desc": _mluc("Display P3 Linear"),
        b"cprt": _mluc("Public domain"),
        b"wtpt": _xyz_tag(D50_XYZ),
        b"rXYZ": _xyz_tag([m[r][0] for r in range(3)]),
        b"gXYZ": _xyz_tag([m[r][1] for r in range(3)]),
        b"bXYZ": _xyz_tag([m[r][2] for r in range(3)]),
        b"rTRC": b"curv\0\0\0\0" + struct.pack(">I", 0),     # count 0 == identity (linear)
        b"chad": b"sf32\0\0\0\0" + b"".join(_s15(adapt[r][c]) for r in range(3) for c in range(3)),
    }
    trc = tags[b"rTRC"]
    order = [b"desc", b"cprt", b"wtpt", b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC", b"chad"]
    table, data, off = b"", b"", 128 + 4 + 12 * len(order)
    shared = {}
    for t in order:
        blob = trc if t in (b"gTRC", b"bTRC") else tags[t]
        key = blob if t in (b"gTRC", b"bTRC") else None
        if key in shared:
            o, n = shared[key]
        else:
            pad = (-len(blob)) % 4
            o, n = off, len(blob)
            data += blob + b"\0" * pad
            off += n + pad
            if key is not None:
                shared[key] = (o, n)
        table += t + struct.pack(">II", o, n)
    body = struct.pack(">I", len(order)) + table + data
    size = 128 + len(body)
    header = bytearray(128)
    struct.pack_into(">I", header, 0, size)
    header[8:12] = bytes([4, 0x40, 0, 0])               # v4.4
    header[12:16] = b"mntr"; header[16:20] = b"RGB "; header[20:24] = b"XYZ "
    header[36:40] = b"acsp"
    struct.pack_into(">3i", header, 68, *(round(c * 65536) for c in D50_XYZ))
    return bytes(header) + body


def colr_box() -> bytes:
    icc = display_p3_linear()
    payload = b"prof" + icc
    return struct.pack(">I", 8 + len(payload)) + b"colr" + payload
