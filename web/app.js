import { patch, looksLikeHeic, AlreadyStyled } from "./patcher.js";

const input = document.getElementById("file");
const drop = document.getElementById("drop");
const list = document.getElementById("out");

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

async function handle(file) {
  const { li, m } = row(file.name);
  try {
    const data = new Uint8Array(await file.arrayBuffer());
    if (!looksLikeHeic(data)) throw new Error("不是 HEIC(可能被 iOS 转成了 JPEG)");
    const { data: out, report } = patch(data);
    const url = URL.createObjectURL(new Blob([out], { type: "image/heic" }));
    const a = document.createElement("a");
    a.className = "btn"; a.href = url;
    a.download = file.name.replace(/\.[^.]+$/, "") + "_styled.heic";
    a.textContent = "下载";
    m.textContent = `完成 · ${report.primary.join("×")} · ${(out.length / 1048576).toFixed(1)} MB`;
    li.append(a);
  } catch (e) {
    const skip = e instanceof AlreadyStyled;
    m.className = skip ? "msg" : "msg bad";
    m.textContent = skip ? "已带风格数据,无需处理" : "无法处理:" + e.message;
  }
}

function take(files) { for (const f of files) handle(f); }
input.addEventListener("change", () => { take(input.files); input.value = ""; });
for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); });
drop.addEventListener("drop", (e) => take(e.dataTransfer.files));
