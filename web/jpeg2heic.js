// Convert a JPEG into a plain HEIC (HEVC) so that patch() can add the style data.
// Needs an HEVC encoder: WebCodecs VideoEncoder in browsers, or an injected one in tests.
import { box, fbox, u16, u32, str, cat, serialiseMeta, patch, HeifError } from "./patcher.js";

const rd16 = (b, o) => (b[o] << 8) | b[o + 1];

export function parseJpeg(b) {
  if (b[0] !== 0xff || b[1] !== 0xd8) throw new HeifError("not a JPEG");
  let p = 2, exif = null, width = 0, height = 0; const icc = [];
  while (p + 4 <= b.length) {
    if (b[p] !== 0xff) { p++; continue; }
    const m = b[p + 1];
    if (m === 0xd8 || m === 0x01 || (m >= 0xd0 && m <= 0xd7) || m === 0xff) { p += m === 0xff ? 1 : 2; continue; }
    if (m === 0xda) break;
    const len = rd16(b, p + 2), seg = b.subarray(p + 4, p + 2 + len);
    if (m === 0xe1 && String.fromCharCode(...seg.subarray(0, 4)) === "Exif" && !exif) exif = seg.slice(6);
    else if (m === 0xe2 && String.fromCharCode(...seg.subarray(0, 11)) === "ICC_PROFILE") icc.push([seg[11], seg.slice(14)]);
    else if (m >= 0xc0 && m <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(m)) { height = rd16(seg, 1); width = rd16(seg, 3); }
    p += 2 + len;
  }
  if (!width) throw new HeifError("JPEG has no frame header");
  icc.sort((a, c) => a[0] - c[0]);
  return { width, height, exif, icc: icc.length ? cat(...icc.map((x) => x[1])) : null, orientation: exif ? tiffOrientation(exif) : 1 };
}

function tiffOrientation(t) {
  const le = t[0] === 0x49, dv = new DataView(t.buffer, t.byteOffset, t.byteLength);
  try {
    const ifd = dv.getUint32(4, le), n = dv.getUint16(ifd, le);
    for (let i = 0; i < n; i++) { const e = ifd + 2 + 12 * i; if (dv.getUint16(e, le) === 0x0112) return dv.getUint16(e + 8, le); }
  } catch { /* fall through */ }
  return 1;
}

// A TIFF stream with an Apple MakerNote that holds no tags yet (patch() adds 0x54).
function syntheticExif(orientation) {
  const mn = cat(str("Apple iOS\0"), new Uint8Array([0, 1, 0x4d, 0x4d]), u16(0), u32(0));
  const ifd0Len = 2 + 3 * 12 + 4, exifOff = 8 + ifd0Len, mnOff = exifOff + 2 + 12 + 4;
  const e = (tag, typ, cnt, val) => cat(u16(tag), u16(typ), u32(cnt), val);
  const ifd0 = cat(u16(3), e(0x010f, 2, 6, u32(mnOff + mn.length)), e(0x0112, 3, 1, cat(u16(orientation), u16(0))), e(0x8769, 4, 1, u32(exifOff)), u32(0));
  const exif = cat(u16(1), e(0x927c, 7, mn.length, u32(mnOff)), u32(0));
  return cat(str("MM\0*"), u32(8), ifd0, exif, mn, str("Apple\0"));
}

const hasAppleMakerNote = (t) => { const s = String.fromCharCode(...t.subarray(0, Math.min(t.length, 4096))); return s.includes("Apple iOS"); };

// EXIF orientation -> HEIF irot (units of 90 degrees counter-clockwise)
const IROT = { 1: 0, 3: 2, 6: 3, 8: 1 };

function colrBox(icc) {
  const p3 = icc && /P3/.test(String.fromCharCode(...icc.subarray(0, Math.min(icc.length, 512))));
  return box("colr", cat(str("nclx"), u16(p3 ? 12 : 1), u16(13), u16(1), new Uint8Array([0])));
}

export async function jpegToHeic(jpeg, encode) {
  const info = parseJpeg(jpeg);
  if (!(info.orientation in IROT)) throw new HeifError("mirrored EXIF orientation is not supported");
  const { width: w, height: h } = info;
  const landscape = w >= h, tw = landscape ? 1024 : 768, th = landscape ? 768 : 1024;
  const main = await encode(w, h, "main");
  const thumb = await encode(tw, th, "thumb");

  const exifTiff = info.exif && hasAppleMakerNote(info.exif) ? info.exif : syntheticExif(info.orientation);
  const props = [], P = (raw) => (props.push(raw), props.length);
  const ispeM = P(fbox("ispe", 0, 0, cat(u32(w), u32(h)))), hvcM = P(main.hvcC), colr = P(colrBox(info.icc));
  const pixi = P(fbox("pixi", 0, 0, new Uint8Array([3, 8, 8, 8])));
  const rot = IROT[info.orientation], irot = rot ? P(box("irot", new Uint8Array([rot]))) : null;
  const ispeT = P(fbox("ispe", 0, 0, cat(u32(tw), u32(th)))), hvcT = P(thumb.hvcC);

  const item = (id, type, data, assoc, hidden) => ({ id, type, name: "", ctype: "", hidden, loc: { kind: "new", data }, props: assoc });
  const rotA = irot ? [[irot, true]] : [];
  const items = new Map([
    [1, item(1, "hvc1", main.sample, [[ispeM, false], [hvcM, true], [colr, false], [pixi, false], ...rotA], false)],
    [2, item(2, "hvc1", thumb.sample, [[ispeT, false], [hvcT, true], [colr, false], [pixi, false], ...rotA], false)],
    [3, item(3, "Exif", cat(u32(0), exifTiff), [], true)],
  ]);
  const hdlr = fbox("hdlr", 0, 0, cat(u32(0), str("pict"), new Uint8Array(12), new Uint8Array(1)));
  const meta = { primary: 1, items, refs: [["thmb", 2, [1]], ["cdsc", 3, [1]]], props, idat: new Uint8Array(0), other: [["hdlr", hdlr]] };

  const ftyp = box("ftyp", cat(str("heic"), u32(0), str("mif1"), str("heic"), str("MiHB")));
  const probe = serialiseMeta(meta, new Map(), (o) => o);
  let cur = ftyp.length + probe.length + 8; const offs = new Map();
  for (const it of items.values()) { offs.set(it.id, cur); cur += it.loc.data.length; }
  const finalMeta = serialiseMeta(meta, offs, (o) => o);
  const heic = cat(ftyp, finalMeta, box("mdat", cat(...[...items.values()].map((i) => i.loc.data))));
  return { heic, synthesizedExif: exifTiff !== info.exif, size: [w, h] };
}

export async function convertAndPatch(jpeg, encode) {
  const { heic, synthesizedExif, size } = await jpegToHeic(jpeg, encode);
  const res = patch(heic);
  return { ...res, synthesizedExif, converted: true, size };
}

// ----- browser encoder (WebCodecs). Returns { hvcC: <full hvcC box>, sample: <length-prefixed> }.
export function webCodecsEncoder(jpegBlob) {
  let bitmapP = null;
  return async function encode(w, h, role) {
    if (typeof VideoEncoder === "undefined") throw new HeifError("此浏览器不支持 WebCodecs,无法在网页里转换 JPEG。请改用「从文件选择」传原始 HEIC。");
    bitmapP ||= createImageBitmap(jpegBlob, { imageOrientation: "none", colorSpaceConversion: "none" });
    const bmp = await bitmapP;
    const canvas = new OffscreenCanvas(w, h), ctx = canvas.getContext("2d");
    ctx.drawImage(bmp, 0, 0, w, h);
    const level = w * h > 35_000_000 ? "L180" : "L153";
    const config = { codec: `hvc1.1.6.${level}.B0`, width: w, height: h, framerate: 1,
      bitrate: Math.round(w * h * (role === "main" ? 1.6 : 4)), latencyMode: "quality", hevc: { format: "hevc" } };
    const sup = await VideoEncoder.isConfigSupported(config).catch(() => ({ supported: false }));
    if (!sup.supported) throw new HeifError("此设备的浏览器不支持 HEVC 编码,无法在网页里转换 JPEG。请改用「从文件选择」传原始 HEIC。");
    let description = null, chunkData = null, failure = null;
    const enc = new VideoEncoder({
      output: (chunk, meta) => { if (meta && meta.decoderConfig && meta.decoderConfig.description) description = new Uint8Array(meta.decoderConfig.description); chunkData = new Uint8Array(chunk.byteLength); chunk.copyTo(chunkData); },
      error: (e) => { failure = e; },
    });
    enc.configure(config);
    const frame = new VideoFrame(canvas, { timestamp: 0 });
    enc.encode(frame, { keyFrame: true });
    frame.close();
    await enc.flush(); enc.close();
    if (failure) throw new HeifError("HEVC 编码失败:" + failure.message);
    if (!description || !chunkData) throw new HeifError("编码器没有返回数据");
    if (chunkData[0] === 0 && chunkData[1] === 0 && (chunkData[2] === 1 || (chunkData[2] === 0 && chunkData[3] === 1))) throw new HeifError("此浏览器的 HEVC 输出格式不受支持");
    return { hvcC: box("hvcC", description), sample: chunkData };
  };
}
