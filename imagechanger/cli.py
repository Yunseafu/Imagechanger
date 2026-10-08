from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .bmff import HeifError, parse_meta
from .patch import AlreadyStyled, is_styled, patch


def looks_like_heic(data: bytes) -> bool:
    return data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"hevc", b"msf1")


def cmd_patch(args) -> int:
    src = Path(args.input)
    data = src.read_bytes()
    if not looks_like_heic(data):
        print("not a HEIC file (iOS may have converted it to JPEG)", file=sys.stderr)
        return 2
    out = Path(args.output) if args.output else src.with_name(src.stem + "_styled.heic")
    try:
        res = patch(data, delta_override=tuple(map(int, args.delta_size.split("x"))) if args.delta_size else None)
    except AlreadyStyled as e:
        print(f"nothing to do: {e}")
        return 3
    except HeifError as e:
        print(f"cannot patch: {e}", file=sys.stderr)
        return 1
    out.write_bytes(res.data)
    print(f"wrote {out}\n" + json.dumps(res.report))
    return 0


def cmd_inspect(args) -> int:
    data = Path(args.input).read_bytes()
    meta, _ = parse_meta(data)
    info = {"styled": is_styled(meta), "primary": meta.primary,
            "items": [{"id": i.item_id, "type": i.item_type.decode("latin1"), "aux": meta.aux_uri(i),
                       "hidden": i.hidden, "mime_or_uri": i.content_type or None}
                      for i in meta.items.values()],
            "refs": [[rt.decode(), f, ts] for rt, f, ts in meta.refs]}
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def cmd_serve(args) -> int:
    from .server import serve
    serve(args.host, args.port)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="imagechanger", description=__doc__)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("patch", help="add Photographic Styles data to a HEIC")
    p.add_argument("input")
    p.add_argument("output", nargs="?")
    p.add_argument("--delta-size", help="WxH of the StyleDeltaMap for unlisted photo sizes")
    p.set_defaults(fn=cmd_patch)
    i = sub.add_parser("inspect", help="list the item graph of a HEIC as JSON")
    i.add_argument("input")
    i.set_defaults(fn=cmd_inspect)
    s = sub.add_parser("serve", help="local HTTP server for iOS Shortcuts")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_serve)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
