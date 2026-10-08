// Browser/Node port of the imagechanger patcher (see imagechanger/*.py). No dependencies.
import { COLR_BOX, TILE_HVCC, TILE_SAMPLE, HALF_C, HALF_D, MATTE_HVCC, MATTE_SAMPLE, XMP } from "./constants.js";

const URI_STYLES = "tag:apple.com,2023:photo:metadata:styles";
const URI_LINEAR = "tag:apple.com,2023:photo:aux:linearthumbnail";
const URI_DELTA = "tag:apple.com,2023:photo:aux:styledeltamap";
const DELTA_SIZES = new Map([["4032x3024", [2880, 2160]], ["5712x4284", [4096, 3072]], ["3088x2316", [2240, 1680]]]);
const TILE = 512;
const URI_TEXTURE = "tag:apple.com,2026:photo:metadata:texture_styles";
const MATTE_NAMES = ["semanticnosematte", "semanticskinmattev2", "semanticnonfaceskinmatte", "semanticlipsmatte", "semanticteethmattev2", "semanticpersonmatte", "semanticglassesmattev2", "semanticeyebrowsmatte", "semantictattoomatte", "semantichandsmatte", "semanticearsmatte", "semanticfaceskinmatte"];
const MATTE_URIS = MATTE_NAMES.map((n) => "tag:apple.com,2026:photo:aux:" + n);
const enc = new TextEncoder(), dec = new TextDecoder();
export { box, fbox, u16, u32, str, cat, serialiseMeta };

export class HeifError extends Error {}
export class AlreadyStyled extends HeifError {}

const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const cat = (...parts) => { const n = parts.reduce((a, p) => a + p.length, 0), o = new Uint8Array(n); let i = 0; for (const p of parts) { o.set(p, i); i += p.length; } return o; };
const u32 = (n) => new Uint8Array([n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]);
const u16 = (n) => new Uint8Array([(n >>> 8) & 255, n & 255]);
const str = (s) => enc.encode(s);
const rd = (b, o, n) => { let v = 0; for (let i = 0; i < n; i++) v = v * 256 + b[o + i]; return v; };
const tag4 = (b, o) => String.fromCharCode(b[o], b[o + 1], b[o + 2], b[o + 3]);
const box = (t, p) => cat(u32(8 + p.length), str(t), p);
const fbox = (t, v, f, p) => box(t, cat(new Uint8Array([v, (f >> 16) & 255, (f >> 8) & 255, f & 255]), p));

function* boxes(b, s = 0, e = b.length) {
  let p = s;
  while (p + 8 <= e) {
    let size = rd(b, p, 4), head = 8;
    if (size === 1) { size = rd(b, p + 8, 8); head = 16; } else if (size === 0) size = e - p;
    if (size < head || p + size > e) throw new HeifError("corrupt box at " + p);
    yield { t: tag4(b, p + 4), b0: p, p0: p + head, b1: p + size };
    p += size;
  }
}
const findBox = (b, t, s, e) => { for (const x of boxes(b, s, e)) if (x.t === t) return x; return null; };

// ------------------------------------------------------------------ meta model
function parseMeta(buf) {
  const m = findBox(buf, "meta");
  if (!m) throw new HeifError("not a HEIF file");
  const meta = { primary: 0, items: new Map(), refs: [], props: [], idat: new Uint8Array(0), other: [] };
  let ipma = [], iloc = new Map();
  for (const c of boxes(buf, m.p0 + 4, m.b1)) {
    const raw = buf.subarray(c.b0, c.b1), v = buf[c.p0];
    if (c.t === "pitm") meta.primary = rd(buf, c.p0 + 4, v === 0 ? 2 : 4);
    else if (c.t === "iinf") {
      for (const e of boxes(buf, c.p0 + 4 + (v === 0 ? 2 : 4), c.b1)) {
        if (e.t !== "infe") continue;
        const ev = buf[e.p0]; if (ev < 2) throw new HeifError("infe < v2");
        const flags = rd(buf, e.p0 + 1, 3), idsz = ev === 2 ? 2 : 4, iid = rd(buf, e.p0 + 4, idsz);
        const type = tag4(buf, e.p0 + 6 + idsz);
        const rest = dec.decode(buf.subarray(e.p0 + 10 + idsz, e.b1)).split("\0");
        meta.items.set(iid, { id: iid, type, name: rest[0] || "", ctype: rest[1] || "", hidden: !!(flags & 1), loc: null, props: [] });
      }
    } else if (c.t === "iloc") iloc = parseIloc(buf, c.p0, v);
    else if (c.t === "iref") {
      const sz = v === 0 ? 2 : 4;
      for (const r of boxes(buf, c.p0 + 4, c.b1)) {
        const from = rd(buf, r.p0, sz), n = rd(buf, r.p0 + sz, 2), to = [];
        for (let i = 0; i < n; i++) to.push(rd(buf, r.p0 + sz + 2 + i * sz, sz));
        meta.refs.push([r.t, from, to]);
      }
    } else if (c.t === "iprp") {
      for (const e of boxes(buf, c.p0, c.b1)) {
        if (e.t === "ipco") meta.props = [...boxes(buf, e.p0, e.b1)].map((x) => buf.slice(x.b0, x.b1));
        else if (e.t === "ipma") ipma = parseIpma(buf, e.p0);
      }
    } else if (c.t === "idat") meta.idat = buf.slice(c.p0, c.b1);
    else meta.other.push([c.t, buf.slice(c.b0, c.b1)]);
  }
  for (const [iid, a] of ipma) if (meta.items.has(iid)) meta.items.get(iid).props = a;
  for (const [iid, l] of iloc) if (meta.items.has(iid)) meta.items.get(iid).loc = l;
  return meta;
}
function parseIloc(b, p0, v) {
  const a = b[p0 + 4], c = b[p0 + 5];
  const offSz = a >> 4, lenSz = a & 15, baseSz = c >> 4, idxSz = v === 1 || v === 2 ? c & 15 : 0;
  let p = p0 + 6; const out = new Map();
  const cnt = rd(b, p, v < 2 ? 2 : 4); p += v < 2 ? 2 : 4;
  for (let i = 0; i < cnt; i++) {
    const iid = rd(b, p, v < 2 ? 2 : 4); p += v < 2 ? 2 : 4;
    let method = 0; if (v === 1 || v === 2) { method = rd(b, p, 2) & 15; p += 2; }
    p += 2; const base = rd(b, p, baseSz); p += baseSz;
    const n = rd(b, p, 2); p += 2; const ext = [];
    for (let e = 0; e < n; e++) { p += idxSz; const o = rd(b, p, offSz); p += offSz; const l = rd(b, p, lenSz); p += lenSz; ext.push([base + o, l]); }
    if (ext.length !== 1) throw new HeifError("multi-extent item " + iid);
    out.set(iid, { kind: method === 1 ? "idat" : "file", off: ext[0][0], len: ext[0][1] });
  }
  return out;
}
function parseIpma(b, p0) {
  const v = b[p0], flags = rd(b, p0 + 1, 3), n = rd(b, p0 + 4, 4); let p = p0 + 8; const rows = [];
  for (let i = 0; i < n; i++) {
    const iid = rd(b, p, v === 0 ? 2 : 4); p += v === 0 ? 2 : 4;
    const k = b[p++], a = [];
    for (let j = 0; j < k; j++) {
      if (flags & 1) { const x = rd(b, p, 2); p += 2; a.push([x & 0x7fff, !!(x & 0x8000)]); }
      else { const x = b[p++]; a.push([x & 0x7f, !!(x & 0x80)]); }
    }
    rows.push([iid, a]);
  }
  return rows;
}
function itemBytes(buf, meta, it) {
  if (!it.loc) throw new HeifError("item " + it.id + " has no data");
  if (it.loc.kind === "file") return buf.subarray(it.loc.off, it.loc.off + it.loc.len);
  if (it.loc.kind === "idat") return meta.idat.subarray(it.loc.off, it.loc.off + it.loc.len);
  return it.loc.data;
}
const propBox = (meta, it, kind) => { for (const [i] of it.props) { const r = meta.props[i - 1]; if (tag4(r, 4) === kind) return r; } return null; };
const propIndex = (meta, it, kind) => { for (const [i] of it.props) if (tag4(meta.props[i - 1], 4) === kind) return i; return null; };
const auxUri = (meta, it) => { const r = propBox(meta, it, "auxC"); return r ? dec.decode(r.subarray(12)).split("\0")[0] : null; };
const addProp = (meta, raw) => (meta.props.push(raw), meta.props.length);
function addItem(meta, type, data, o = {}) {
  const id = Math.max(0, ...meta.items.keys()) + 1;
  const it = { id, type, name: o.name || "", ctype: o.ctype || "", hidden: true, loc: null, props: o.props || [] };
  if (data) it.loc = o.idat ? { kind: "idat", off: meta.idat.length, len: data.length } : { kind: "new", data };
  if (data && o.idat) meta.idat = cat(meta.idat, data);
  meta.items.set(id, it);
  if (o.ref) meta.refs.push([o.ref, id, o.to]);
  return id;
}
function serialiseMeta(meta, newOff, shift) {
  const items = [...meta.items.values()], big = Math.max(...meta.items.keys()) > 0xffff, ids = big ? 4 : 2;
  const idb = (n) => (big ? u32(n) : u16(n));
  const infes = items.map((it) => {
    let body = cat(new Uint8Array([big ? 3 : 2, 0, 0, it.hidden ? 1 : 0]), idb(it.id), u16(0), str(it.type), str(it.name), new Uint8Array(1));
    if (it.type === "mime" || it.type === "uri ") body = cat(body, str(it.ctype), new Uint8Array(1));
    return box("infe", body);
  });
  const iinf = fbox("iinf", big ? 1 : 0, 0, cat(idb(items.length), ...infes));
  const located = items.filter((i) => i.loc);
  const rows = located.map((it) => {
    const method = it.loc.kind === "idat" ? 1 : 0;
    const off = it.loc.kind === "file" ? shift(it.loc.off) : it.loc.kind === "new" ? newOff.get(it.id) || 0 : it.loc.off;
    const len = it.loc.kind === "new" ? it.loc.data.length : it.loc.len;
    return cat(idb(it.id), u16(method), u16(0), u16(1), u32(off), u32(len));
  });
  const iloc = fbox("iloc", big ? 2 : 1, 0, cat(new Uint8Array([0x44, 0]), big ? u32(located.length) : u16(located.length), ...rows));
  const iref = meta.refs.length ? fbox("iref", big ? 1 : 0, 0, cat(...meta.refs.map(([t, f, to]) => box(t, cat(idb(f), u16(to.length), ...to.map(idb)))))) : new Uint8Array(0);
  const wide = meta.props.length > 127, withProps = items.filter((i) => i.props.length);
  const ipma = fbox("ipma", big ? 1 : 0, wide ? 1 : 0, cat(u32(withProps.length), ...withProps.map((it) =>
    cat(idb(it.id), new Uint8Array([it.props.length]), ...it.props.map(([i, e]) => (wide ? u16((e ? 0x8000 : 0) | i) : new Uint8Array([(e ? 0x80 : 0) | i])))))));
  const iprp = box("iprp", cat(box("ipco", cat(...meta.props)), ipma));
  const pitm = fbox("pitm", 0, 0, u16(meta.primary));
  const idat = meta.idat.length ? box("idat", meta.idat) : new Uint8Array(0);
  const hdlr = meta.other.filter(([k]) => k === "hdlr").map(([, r]) => r), rest = meta.other.filter(([k]) => k !== "hdlr").map(([, r]) => r);
  return fbox("meta", 0, 0, cat(...hdlr, pitm, iinf, iref, iprp, iloc, idat, ...rest));
}
function assemble(buf, meta, newFtyp) {
  const bs = [...boxes(buf)], oF = bs.find((x) => x.t === "ftyp"), oM = bs.find((x) => x.t === "meta");
  const dF = newFtyp.length - (oF.b1 - oF.b0);
  const shiftWith = (dM) => (o) => o + (o >= oF.b1 ? dF : 0) + (o >= oM.b1 ? dM : 0);
  const fresh = [...meta.items.values()].filter((i) => i.loc && i.loc.kind === "new");
  const probe = serialiseMeta(meta, new Map(), shiftWith(0));
  const dM = probe.length - (oM.b1 - oM.b0);
  let head = 0; for (const x of bs) head += x.t === "ftyp" ? newFtyp.length : x.t === "meta" ? probe.length : x.b1 - x.b0;
  const offs = new Map(); let cur = head + 8;
  for (const it of fresh) { offs.set(it.id, cur); cur += it.loc.data.length; }
  const finalMeta = serialiseMeta(meta, offs, shiftWith(dM));
  const out = [];
  for (const x of bs) out.push(x.t === "ftyp" ? newFtyp : x.t === "meta" ? finalMeta : buf.subarray(x.b0, x.b1));
  if (fresh.length) out.push(box("mdat", cat(...fresh.map((i) => i.loc.data))));
  return cat(...out);
}

// ------------------------------------------------------------------ exif
function styleRecord() { return plistBinary({ 0: 1, 1: 0.0, 2: 0.0, 3: 1.0, 4: 1, 5: 1, 6: 4, 7: 0 }, new Set(["1", "2", "3"])); }
const TYPE_SZ = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8 };
function addStyleTag(item, record) {
  const dv = (a) => new DataView(a.buffer, a.byteOffset, a.byteLength);
  const tiffOff = 4 + dv(item).getUint32(0);
  const head = item.slice(0, tiffOff); let t = item.slice(tiffOff);
  const le = t[0] === 0x49; if (!le && t[0] !== 0x4d) throw new HeifError("no TIFF header");
  const find = (ifd, tag) => { const n = dv(t).getUint16(ifd, le); for (let i = 0; i < n; i++) { const p = ifd + 2 + 12 * i; if (dv(t).getUint16(p, le) === tag) return p; } return null; };
  const ifd0 = dv(t).getUint32(4, le), p = find(ifd0, 0x8769); if (p === null) throw new HeifError("no ExifIFD");
  const mp = find(dv(t).getUint32(p + 8, le), 0x927c); if (mp === null) throw new HeifError("no MakerNote");
  const mnLen = dv(t).getUint32(mp + 4, le), mnOff = dv(t).getUint32(mp + 8, le);
  const mn = t.slice(mnOff, mnOff + mnLen);
  if (dec.decode(mn.subarray(0, 9)) !== "Apple iOS") throw new HeifError("MakerNote is not Apple's");
  const me = !(mn[12] === 0x49), md = dv(mn), H = 14, count = md.getUint16(H, !me);
  let ents = [];
  for (let i = 0; i < count; i++) { const o = H + 2 + 12 * i; ents.push({ tag: md.getUint16(o, !me), typ: md.getUint16(o + 2, !me), cnt: md.getUint32(o + 4, !me), val: mn.slice(o + 8, o + 12) }); }
  const oldEnd = H + 2 + 12 * count + 4, nxt = mn.slice(oldEnd - 4, oldEnd);
  ents = ents.filter((x) => x.tag !== 0x54);
  const grow = 12 * (1 - (count - ents.length)), tail = mn.slice(oldEnd);
  for (const x of ents) if ((TYPE_SZ[x.typ] || 1) * x.cnt > 4) { const d = dv(x.val); d.setUint32(0, d.getUint32(0, !me) + grow, !me); }
  const recOff = oldEnd + grow + tail.length, ov = new Uint8Array(4); dv(ov).setUint32(0, recOff, !me);
  ents.push({ tag: 0x54, typ: 7, cnt: record.length, val: ov });
  ents.sort((a, b) => a.tag - b.tag);
  const hdr = mn.slice(0, H), cntB = new Uint8Array(2); dv(cntB).setUint16(0, ents.length, !me);
  const entB = ents.map((x) => { const e = new Uint8Array(12); const d = dv(e); d.setUint16(0, x.tag, !me); d.setUint16(2, x.typ, !me); d.setUint32(4, x.cnt, !me); e.set(x.val, 8); return e; });
  let nm = cat(hdr, cntB, ...entB, nxt, tail, record); if (nm.length % 2) nm = cat(nm, new Uint8Array(1));
  if (t.length % 2) t = cat(t, new Uint8Array(1));
  const newOff = t.length; t = cat(t, nm);
  dv(t).setUint32(mp + 4, nm.length, le); dv(t).setUint32(mp + 8, newOff, le);
  return cat(head, t);
}

const DEVICE = [[0x010f, "Apple"], [0x0110, "iPhone 18 Pro"], [0x0131, "27.0"]];
// Overwrite the ASCII Make/Model/Software tags of IFD0 (absent tags stay absent).
export function setDevice(item) {
  const d = (a) => new DataView(a.buffer, a.byteOffset, a.byteLength);
  const tiffOff = 4 + d(item).getUint32(0), head = item.slice(0, tiffOff); let t = item.slice(tiffOff);
  const le = t[0] === 0x49, ifd0 = d(t).getUint32(4, le);
  for (const [tag, text] of DEVICE) {
    const n = d(t).getUint16(ifd0, le); let pos = null;
    for (let i = 0; i < n; i++) { const p = ifd0 + 2 + 12 * i; if (d(t).getUint16(p, le) === tag) pos = p; }
    if (pos === null) continue;
    const raw = cat(str(text), new Uint8Array(1));
    d(t).setUint16(pos + 2, 2, le); d(t).setUint32(pos + 4, raw.length, le);
    if (raw.length <= 4) { t.fill(0, pos + 8, pos + 12); t.set(raw, pos + 8); }
    else { if (t.length % 2) t = cat(t, new Uint8Array(1)); d(t).setUint32(pos + 8, t.length, le); t = cat(t, raw); }
  }
  return cat(head, t);
}

// ------------------------------------------------------------------ binary plist
// Supports: int (non-negative), double, boolean, Uint8Array, string, plain object (string keys).
function plistBinary(root, forceReal = new Set()) {
  const objs = []; const refOf = new Map();
  const seen = new Map();
  const add = (v, key) => {
    const sk = typeof v === "object" && !(v instanceof Uint8Array) ? null
      : (v instanceof Uint8Array ? "d" + v.join(",") : typeof v + (typeof v === "number" ? (Number.isInteger(v) && !forceReal.has(key) ? "i" : "r") : "") + v);
    if (sk !== null && seen.has(sk)) return seen.get(sk);
    const idx = objs.length; objs.push(null);
    if (sk !== null) seen.set(sk, idx);
    if (v instanceof Uint8Array) objs[idx] = { k: "data", v };
    else if (typeof v === "boolean") objs[idx] = { k: "bool", v };
    else if (typeof v === "string") objs[idx] = { k: "str", v };
    else if (typeof v === "number") objs[idx] = { k: Number.isInteger(v) && !forceReal.has(key) ? "int" : "real", v };
    else { const keys = Object.keys(v); const kr = keys.map((k) => add(k)); const vr = keys.map((k) => add(v[k], k)); objs[idx] = { k: "dict", kr, vr }; }
    return idx;
  };
  add(root);
  const rs = objs.length < 256 ? 1 : 2;
  const refB = (n) => (rs === 1 ? new Uint8Array([n]) : u16(n));
  const lenHead = (marker, n) => {
    if (n < 15) return new Uint8Array([marker | n]);
    const sz = n < 256 ? [0x10, n] : [0x11, n >> 8, n & 255];
    return cat(new Uint8Array([marker | 15]), new Uint8Array(sz));
  };
  const enc1 = objs.map((o) => {
    switch (o.k) {
      case "bool": return new Uint8Array([o.v ? 9 : 8]);
      case "int": { const n = o.v; if (n < 256) return new Uint8Array([0x10, n]); if (n < 65536) return cat(new Uint8Array([0x11]), u16(n)); if (n < 2 ** 32) return cat(new Uint8Array([0x12]), u32(n)); const b = new Uint8Array(9); b[0] = 0x13; new DataView(b.buffer).setBigUint64(1, BigInt(n)); return b; }
      case "real": { const b = new Uint8Array(9); b[0] = 0x23; new DataView(b.buffer).setFloat64(1, o.v); return b; }
      case "data": return cat(lenHead(0x40, o.v.length), o.v);
      case "str": return cat(lenHead(0x50, o.v.length), str(o.v));
      case "dict": return cat(lenHead(0xd0, o.kr.length), ...o.kr.map(refB), ...o.vr.map(refB));
    }
  });
  const body = cat(new Uint8Array([...str("bplist00")]), ...enc1);
  const offs = []; let p = 8; for (const e of enc1) { offs.push(p); p += e.length; }
  const offSz = p < 256 ? 1 : p < 65536 ? 2 : 4;
  const offB = (n) => (offSz === 1 ? new Uint8Array([n]) : offSz === 2 ? u16(n) : u32(n));
  const trailer = new Uint8Array(32); const td = new DataView(trailer.buffer);
  trailer[6] = offSz; trailer[7] = rs; td.setBigUint64(8, BigInt(objs.length)); td.setBigUint64(16, 0n); td.setBigUint64(24, BigInt(p));
  return cat(body, ...offs.map(offB), trailer);
}

// ------------------------------------------------------------------ styles
const GAIN = 14.565662384033203;
function halfBlob(code, count) { const b = new Uint8Array(count * 2); for (let i = 0; i < count; i++) { b[2 * i] = code & 255; b[2 * i + 1] = code >> 8; } return b; }
function identityField() {
  const cell = new Uint8Array(30 * 2); // 10 terms x RGB half floats; 1.0 = 0x3C00
  const idx = [3, 7, 11]; // constant term occupies 0..2; R term 3..5 -> R at 3; G term 6..8 -> G at 7; B term 9..11 -> B at 11
  for (const i of idx) { cell[2 * i + 1] = 0x3c; }
  const out = new Uint8Array(cell.length * 864); for (let r = 0; r < 864; r++) out.set(cell, r * cell.length); return out;
}
function toneCurve() { const b = new Uint8Array(516); b.set([1, 1, 0, 0]); for (let i = 0; i < 256; i++) { const v = Math.round((i * 65535) / 255); b[4 + 2 * i] = v & 255; b[5 + 2 * i] = v >> 8; } return b; }
function buildStyles(personMasks) {
  const tone = { blackPoint: 0.0, p02: 0.004, p10: 0.004, p25: 0.024, p50: 0.16, p75: 0.312, p98: 0.788, whitePoint: 0.932, highKey: 0.7 };
  const linear = {}; for (const [k, v] of Object.entries(tone)) linear[k] = k === "highKey" ? 0.964 : v * 0.166;
  const empty = (hk) => ({ blackPoint: 0.0, p02: 0.0, p10: 0.0, p25: 0.0, p50: 0.0, p75: 0.0, p98: 0.0, whitePoint: 0.0, highKey: hk });
  const six = {};
  for (const n of ["ToneMappedImageSkinBased", "ToneMappedImagePersonSegmentBased", "ToneMappedImageRedChannelSkinBased", "ToneMappedImageGreenChannelSkinBased", "ToneMappedImageBlueChannelSkinBased", "LinearImageSkinBased", "LinearImagePersonSegmentBased"]) six[n] = empty(1.0);
  six.LinearGTCImage = empty(0.0); six.ToneMappedImage = tone; six.LinearImage = linear;
  const reals = new Set(["4", "h", "j", "OriginalRangeMin", "OriginalRangeMax", "Gain", "PeopleRatio", "SkinRatio", "PersonMasksValidHint", ...Object.keys(tone), ...Object.keys(six)]);
  const pl = {
    0: 16, 1: identityField(), 2: true, 3: toneCurve(), 4: 5.384615421295166, 5: 0, 6: six,
    7: { PeopleRatio: 0.0, SkinRatio: 0.0, PersonMasksValidHint: personMasks ? 1.0 : -1.0 },
    c: halfBlob(HALF_C, 1024), d: halfBlob(HALF_D, 1024), e: 32, f: 32, g: 0x4c303068, h: GAIN / 4,
    i: { OriginalRangeMin: -0.0019588470458984375, OriginalRangeMax: 0.08447265625, Gain: GAIN }, j: 1.0, k: false, l: false,
  };
  return plistBinary(pl, reals);
}

// ------------------------------------------------------------------ patch
const ispe = (w, h) => fbox("ispe", 0, 0, cat(u32(w), u32(h)));
const auxc = (uri) => fbox("auxC", 0, 0, cat(str(uri), new Uint8Array(1)));
const PIXI = fbox("pixi", 0, 0, new Uint8Array([3, 10, 10, 10]));

export function looksLikeHeic(d) { return d.length > 12 && tag4(d, 4) === "ftyp" && ["heic", "heix", "mif1", "hevc", "msf1"].includes(tag4(d, 8)); }

export function isStyled(buf) { const m = parseMeta(buf); return [...m.items.values()].some((i) => i.type === "uri " && i.ctype === URI_STYLES); }

const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
const crc32 = (b) => { let c = 0xffffffff; for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
const hasType = (meta, ctype) => [...meta.items.values()].some((i) => i.type === "uri " && i.ctype === ctype);

function addTexture(meta, buf, primary, targets, rot) {
  const present = new Set([...meta.items.values()].map((i) => auxUri(meta, i)));
  const missing = MATTE_URIS.filter((u) => !present.has(u));
  if (missing.length) {
    const pI = addProp(meta, ispe(768, 576)), pP = addProp(meta, fbox("pixi", 0, 0, new Uint8Array([1, 8]))), pH = addProp(meta, b64(MATTE_HVCC));
    const sample = b64(MATTE_SAMPLE), ids = new Map();
    for (const uri of missing) {
      const pA = addProp(meta, auxc(uri));
      ids.set(uri, addItem(meta, "hvc1", sample, { ref: "auxl", to: targets, props: [[pI, false], [pP, false], [pA, true], [pH, true], ...rot] }));
    }
    for (const uri of missing) addItem(meta, "mime", b64(XMP), { ctype: "application/rdf+xml", ref: "cdsc", to: [ids.get(uri)] });
  }
  const tiles = meta.refs.filter(([t, f]) => t === "dimg" && f === primary.id)[0];
  const src = tiles ? meta.items.get(tiles[2][0]) : primary;
  const seed = crc32(itemBytes(buf, meta, src)) % 256;
  const plist = plistBinary({ Preset: "Standard", CaptureType: "LF", CaptureMode: "Still", PortType: "PortTypeBack", HardwareModel: "iPhone19,2", TextureStylePeopleDataVersion: 3, FilmGrainSeed: seed });
  addItem(meta, "uri ", plist, { name: "metadata", ctype: URI_TEXTURE, ref: "cdsc", to: targets });
  return { mattes: missing.length, seed };
}

export function patch(buf, deltaOverride = null, grain = true, device = true) {
  const meta = parseMeta(buf);
  const primary = meta.items.get(meta.primary); if (!primary) throw new HeifError("primary item missing");
  if (hasType(meta, URI_STYLES)) {
    if (!grain || hasType(meta, URI_TEXTURE)) throw new AlreadyStyled("photo already carries Photographic Style data");
    const ir = propIndex(meta, primary, "irot");
    const tg = [meta.primary, ...[...meta.items.values()].filter((i) => i.type === "tmap").slice(0, 1).map((i) => i.id)];
    const note = addTexture(meta, buf, primary, tg, ir ? [[ir, true]] : []);
    const f0 = [...boxes(buf)].find((x) => x.t === "ftyp");
    return { data: assemble(buf, meta, buf.slice(f0.b0, f0.b1)), report: { texture: note, textureOnly: true } };
  }
  const pi = propBox(meta, primary, "ispe"); if (!pi) throw new HeifError("primary has no ispe");
  const pw = rd(pi, 12, 4), ph = rd(pi, 16, 4);
  const size = deltaOverride || DELTA_SIZES.get(`${pw}x${ph}`) || (DELTA_SIZES.has(`${ph}x${pw}`) ? DELTA_SIZES.get(`${ph}x${pw}`).slice().reverse() : null);
  if (!size) throw new HeifError(`no StyleDeltaMap size known for a ${pw}x${ph} primary`);
  const [dw, dh] = size, cols = Math.ceil(dw / TILE), rows = Math.ceil(dh / TILE);
  const th = meta.refs.find(([t, f, to]) => t === "thmb" && to.includes(meta.primary));
  if (!th) throw new HeifError("photo has no embedded thumbnail to reuse");
  const thumb = meta.items.get(th[1]);
  const tH = propBox(meta, thumb, "hvcC"), tI = propBox(meta, thumb, "ispe"), tP = propBox(meta, thumb, "pixi");
  if (!tH || !tI) throw new HeifError("thumbnail lacks hvcC/ispe");
  const thumbSample = itemBytes(buf, meta, thumb);
  const exif = [...meta.items.values()].find((i) => i.type === "Exif"); if (!exif) throw new HeifError("photo has no Exif item");
  const irot = propIndex(meta, primary, "irot"), rot = irot ? [[irot, true]] : [];
  const targets = [meta.primary, ...[...meta.items.values()].filter((i) => i.type === "tmap").slice(0, 1).map((i) => i.id)];

  const pColr = addProp(meta, b64(COLR_BOX)), pPixi = addProp(meta, PIXI), pDI = addProp(meta, ispe(dw, dh)), pDA = addProp(meta, auxc(URI_DELTA));
  const pTI = addProp(meta, ispe(TILE, TILE)), pTH = addProp(meta, b64(TILE_HVCC)), pLI = addProp(meta, tI);
  const same = !tP || (tP.length === PIXI.length && tP.every((v, i) => v === PIXI[i]));
  const pLP = same ? pPixi : addProp(meta, tP), pLA = addProp(meta, auxc(URI_LINEAR)), pLH = addProp(meta, tH);

  const lin = addItem(meta, "hvc1", thumbSample, { ref: "auxl", to: targets, props: [[pLI, false], ...rot, [pLP, false], [pLA, true], [pLH, true]] });
  const sample = b64(TILE_SAMPLE), tiles = [];
  for (let i = 0; i < rows * cols; i++) tiles.push(addItem(meta, "hvc1", sample, { props: [[pTI, true], [pColr, true], [pTH, true]] }));
  const desc = cat(new Uint8Array([0, 0, rows - 1, cols - 1]), u16(dw), u16(dh));
  const grid = addItem(meta, "grid", desc, { idat: true, ref: "auxl", to: targets, props: [[pColr, true], [pDI, false], ...rot, [pPixi, false], [pDA, true]] });
  meta.refs.push(["dimg", grid, tiles]);
  const mattes = [...meta.items.values()].some((i) => (auxUri(meta, i) || "").endsWith("portraiteffectsmatte"));
  const sid = addItem(meta, "uri ", buildStyles(mattes), { name: "metadata", ctype: URI_STYLES, ref: "cdsc", to: targets });
  const texture = grain ? addTexture(meta, buf, primary, targets, rot) : "off";
  const exifData = itemBytes(buf, meta, exif);
  exif.loc = { kind: "new", data: addStyleTag(device ? setDevice(exifData) : exifData, styleRecord()) };

  const f = [...boxes(buf)].find((x) => x.t === "ftyp"), body = buf.subarray(f.b0 + 8, f.b1);
  const brands = []; for (let i = 8; i < body.length; i += 4) brands.push(tag4(body, i));
  const at = brands.includes("MiHB") ? brands.indexOf("MiHB") + 1 : brands.length;
  brands.splice(at, 0, ...["MiHA", "heix"].filter((b) => !brands.includes(b)));
  const ftyp = box("ftyp", cat(body.subarray(0, 8), ...brands.map(str)));
  return { data: assemble(buf, meta, ftyp), report: { primary: [pw, ph], deltaMap: [dw, dh], tiles: [rows, cols], linear: lin, styles: sid, grid, texture } };
}
