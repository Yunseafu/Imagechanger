"""Add the Photographic Styles data set to a HEIC that lacks it (pixels are untouched)."""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import icc, matte_tile, neutral_tile, styles, texture
from .assemble import assemble
from .bmff import HeifError, Meta, box, full_box, item_bytes, iter_boxes, parse_meta
from .exif import add_style_tag, set_device

# StyleDeltaMap size for a primary of a given stored size (sizes seen in native files).
DELTA_SIZES = {(4032, 3024): (2880, 2160), (5712, 4284): (4096, 3072), (3088, 2316): (2240, 1680)}
TILE = 512
PIXI_10BIT = full_box(b"pixi", 0, 0, bytes([3, 10, 10, 10]))
EXTRA_BRANDS = (b"MiHA", b"heix")


class AlreadyStyled(HeifError):
    pass


@dataclass
class Result:
    data: bytes
    report: dict


def ispe(w: int, h: int) -> bytes:
    return full_box(b"ispe", 0, 0, struct.pack(">II", w, h))


def auxc(uri: str) -> bytes:
    return full_box(b"auxC", 0, 0, uri.encode("ascii") + b"\0")


def read_ispe(raw: bytes) -> tuple[int, int]:
    return struct.unpack(">II", raw[12:20])


def delta_size(w: int, h: int):
    if (w, h) in DELTA_SIZES:
        return DELTA_SIZES[(w, h)]
    if (h, w) in DELTA_SIZES:
        dw, dh = DELTA_SIZES[(h, w)]
        return dh, dw
    return None


def with_style_brands(ftyp_box: bytes) -> bytes:
    body = ftyp_box[8:]
    major, minor, brands = body[:4], body[4:8], [body[i:i + 4] for i in range(8, len(body), 4)]
    missing = [b for b in EXTRA_BRANDS if b not in brands]
    at = brands.index(b"MiHB") + 1 if b"MiHB" in brands else len(brands)
    brands[at:at] = missing
    return box(b"ftyp", major + minor + b"".join(brands))


def is_styled(meta: Meta) -> bool:
    return any(i.item_type == b"uri " and i.content_type == styles.URI_STYLES
               for i in meta.items.values())


def has_texture(meta: Meta) -> bool:
    return any(i.item_type == b"uri " and i.content_type == texture.URI_TEXTURE for i in meta.items.values())


def seed_source(meta: Meta, primary):
    """Bytes that identify the photo: its first tile when the primary is a grid, else the image."""
    tiles = meta.refs_from(b"dimg", primary.item_id)
    return meta.items[tiles[0]] if tiles else primary


def patch(buf: bytes, *, tone_stats: dict | None = None, delta_override=None, grain: bool = True, device: bool = True) -> Result:
    meta, _span = parse_meta(buf)
    primary = meta.items.get(meta.primary)
    if primary is None:
        raise HeifError("primary item missing")
    if is_styled(meta):
        # Already has the palette (iPhone 16+ native, or patched earlier): only grain may be missing.
        if not grain or has_texture(meta):
            raise AlreadyStyled("photo already carries Photographic Style data")
        irot_idx = meta.prop_index(primary, b"irot")
        targets = [meta.primary] + [i.item_id for i in meta.by_type(b"tmap")[:1]]
        note = add_texture(meta, buf, primary, targets, [(irot_idx, True)] if irot_idx else [])
        ftyp = next(b for b in iter_boxes(buf) if b[0] == b"ftyp")
        return Result(assemble(buf, meta, buf[ftyp[1]:ftyp[3]]), {"texture": note, "texture_only": True})
    ispe_raw = meta.prop_box(primary, b"ispe")
    if ispe_raw is None:
        raise HeifError("primary item has no ispe")
    pw, ph = read_ispe(ispe_raw)
    size = delta_override or delta_size(pw, ph)
    if size is None:
        raise HeifError(f"no StyleDeltaMap size known for a {pw}x{ph} primary; pass delta_override")
    dw, dh = size
    cols, rows = -(-dw // TILE), -(-dh // TILE)

    thumbs = [f for rt, f, ts in meta.refs if rt == b"thmb" and meta.primary in ts]
    if not thumbs:
        raise HeifError("photo has no embedded thumbnail to reuse as the linear thumbnail")
    thumb = meta.items[thumbs[0]]
    t_hvcc, t_ispe, t_pixi = (meta.prop_box(thumb, k) for k in (b"hvcC", b"ispe", b"pixi"))
    if not (t_hvcc and t_ispe):
        raise HeifError("thumbnail lacks hvcC/ispe")
    thumb_sample = item_bytes(buf, meta, thumb)

    exif = next(iter(meta.by_type(b"Exif")), None)
    if exif is None:
        raise HeifError("photo has no Exif item (needed for the style MakerNote record)")

    irot_idx = meta.prop_index(primary, b"irot")
    rot = [(irot_idx, True)] if irot_idx else []
    targets = [meta.primary] + [i.item_id for i in meta.by_type(b"tmap")[:1]]

    # ---- properties (appended, so no existing index moves)
    p_colr = meta.add_property(icc.colr_box())
    p_pixi = meta.add_property(PIXI_10BIT)
    p_d_ispe = meta.add_property(ispe(dw, dh))
    p_d_auxc = meta.add_property(auxc(styles.URI_STYLE_DELTA))
    p_t_ispe = meta.add_property(ispe(TILE, TILE))
    p_t_hvcc = meta.add_property(neutral_tile.HVCC)
    p_l_ispe = meta.add_property(t_ispe)
    p_l_pixi = p_pixi if (t_pixi in (None, PIXI_10BIT)) else meta.add_property(t_pixi)
    p_l_auxc = meta.add_property(auxc(styles.URI_LINEAR_THUMB))
    p_l_hvcc = meta.add_property(t_hvcc)

    lin = meta.add_item(b"hvc1", thumb_sample, ref_type=b"auxl", ref_to=targets,
                        props=[(p_l_ispe, False)] + rot + [(p_l_pixi, False), (p_l_auxc, True), (p_l_hvcc, True)])
    tiles = [meta.add_item(b"hvc1", neutral_tile.SAMPLE,
                           props=[(p_t_ispe, True), (p_colr, True), (p_t_hvcc, True)])
             for _ in range(rows * cols)]
    descriptor = bytes([0, 0, rows - 1, cols - 1]) + struct.pack(">HH", dw, dh)
    grid = meta.add_item(b"grid", descriptor, in_idat=True, ref_type=b"auxl", ref_to=targets,
                         props=[(p_colr, True), (p_d_ispe, False)] + rot + [(p_pixi, False), (p_d_auxc, True)])
    meta.refs.append((b"dimg", grid, tiles))
    plist = styles.build_styles(tone_stats, person_masks=_has_mattes(meta))
    sid = meta.add_item(b"uri ", plist, name="metadata", content_type=styles.URI_STYLES,
                        ref_type=b"cdsc", ref_to=targets)

    grain_report = add_texture(meta, buf, primary, targets, rot) if grain else "off"

    exif_data = item_bytes(buf, meta, exif)
    exif.loc = ("new", add_style_tag(set_device(exif_data) if device else exif_data))

    ftyp = next(b for b in iter_boxes(buf) if b[0] == b"ftyp")
    out = assemble(buf, meta, with_style_brands(buf[ftyp[1]:ftyp[3]]))
    return Result(out, {"primary": [pw, ph], "delta_map": [dw, dh], "tiles": [rows, cols],
                        "linear_thumb_item": lin, "styles_item": sid, "grid_item": grid,
                        "texture": grain_report})


def _has_mattes(meta: Meta) -> bool:
    return any((meta.aux_uri(i) or "").endswith("portraiteffectsmatte") for i in meta.items.values())


def add_texture(meta: Meta, buf: bytes, primary, targets, rot) -> str:
    """Add texture_styles and any missing 2026 matte (+XMP sidecar) items."""
    present = {meta.aux_uri(i) for i in meta.items.values()}
    missing = [u for u in texture.MATTE_URIS if u not in present]
    if missing:
        p_ispe = meta.add_property(ispe(*texture.MATTE_SIZE))
        p_pixi = meta.add_property(full_box(b"pixi", 0, 0, bytes([1, 8])))
        p_hvcc = meta.add_property(matte_tile.HVCC)
        ids = {}
        for uri in missing:
            p_aux = meta.add_property(auxc(uri))
            ids[uri] = meta.add_item(b"hvc1", matte_tile.SAMPLE, ref_type=b"auxl", ref_to=targets,
                                     props=[(p_ispe, False), (p_pixi, False), (p_aux, True), (p_hvcc, True)] + rot)
        for uri in missing:
            meta.add_item(b"mime", texture.XMP, content_type="application/rdf+xml", ref_type=b"cdsc",
                          ref_to=[ids[uri]])
    seed = texture.grain_seed(item_bytes(buf, meta, seed_source(meta, primary)))
    meta.add_item(b"uri ", texture.texture_plist(seed), name="metadata", content_type=texture.URI_TEXTURE,
                  ref_type=b"cdsc", ref_to=targets)
    return f"added {len(missing)} mattes, seed {seed}"
