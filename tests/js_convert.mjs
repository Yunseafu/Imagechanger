// usage: node tests/js_convert.mjs in.jpg out.heic dir  (dir holds main/thumb .hvcc and .sample files)
import { readFileSync, writeFileSync } from "node:fs";
import { convertAndPatch } from "../web/jpeg2heic.js";
const [, , inp, out, dir] = process.argv;
const encode = async (w, h, role) => ({
  hvcC: new Uint8Array(readFileSync(`${dir}/${role}.hvcc`)),
  sample: new Uint8Array(readFileSync(`${dir}/${role}.sample`)),
});
const res = await convertAndPatch(new Uint8Array(readFileSync(inp)), encode);
writeFileSync(out, res.data);
console.log(JSON.stringify({ synthesized: res.synthesizedExif, size: res.size, report: res.report }));
