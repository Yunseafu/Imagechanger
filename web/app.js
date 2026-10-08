import { patch, looksLikeHeic, AlreadyStyled } from "./patcher.js";

const list = document.getElementById("out");
const drop = document.getElementById("drop");

const baseName = (n) => n.replace(/\.[^.]+$/, "");
const isJpeg = (d) => d[0] === 0xff && d[1] === 0xd8;
const isMov = (d) => d.length > 12 && String.fromCharCode(...d.subarray(4, 8)) === "ftyp" && String.fromCharCode(...d.subarray(8, 12)) === "qt  ";

function row(name) {
  const li = document.createElement("li");
  const left = document.createElement("div");
  const n = document.createElement("div");
  n.className = "name"; n.textContent = name;
  const m = document.createElement("div");
  m.className = "msg"; m.textContent = "处理中…";
  left.append(n, m);
  li.append(left);
  list.prepend(li);
  return { li, m };
}
const bad = (m, text) => { m.className = "msg bad"; m.textContent = text; };
function download(li, bytes, type, filename) {
  const a = document.createElement("a");
  a.className = "btn"; a.href = URL.createObjectURL(new Blob([bytes], { type })); a.download = filename; a.textContent = "下载";
  li.append(a);
}

async function run(files) {
  const items = [];
  for (const f of files) items.push({ f, data: new Uint8Array(await f.arrayBuffer()) });
  const photos = items.filter((x) => looksLikeHeic(x.data));
  const stems = new Set(photos.map((x) => baseName(x.f.name).toLowerCase()));

  for (const { f, data } of items) {
    const { li, m } = row(f.name);
    try {
      if (looksLikeHeic(data)) {
        const { data: out, report } = patch(data);
        const hasVideo = items.some((y) => isMov(y.data) && baseName(y.f.name).toLowerCase() === baseName(f.name).toLowerCase());
        m.textContent = `完成 · ${report.primary.join("×")} · ${(out.length / 1048576).toFixed(1)} MB` + (hasVideo ? " · Live Photo 的静态图" : "");
        download(li, out, "image/heic", baseName(f.name) + "_styled.heic");
      } else if (isMov(data)) {
        if (!stems.has(baseName(f.name).toLowerCase())) { bad(m, "这是 Live Photo 的视频,但没选到同名的 HEIC。请连同 HEIC 一起选。"); continue; }
        m.textContent = "Live Photo 视频 · 原样保留,请与处理后的 HEIC 一起存入相册";
        download(li, data, "video/quicktime", baseName(f.name) + "_styled.mov");
      } else if (isJpeg(data)) {
        bad(m, "这是 JPEG,不是 HEIC:iOS 从相册选图时已自动转换。请改用「从文件选择」,见下方说明。");
      } else {
        bad(m, "无法识别的文件类型");
      }
    } catch (e) {
      if (e instanceof AlreadyStyled) m.textContent = "已带风格数据,无需处理";
      else bad(m, "无法处理:" + e.message);
    }
  }
}

for (const id of ["file", "photos"]) {
  const el = document.getElementById(id);
  el.addEventListener("change", () => { run([...el.files]); el.value = ""; });
}
for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); });
drop.addEventListener("drop", (e) => run([...e.dataTransfer.files]));
