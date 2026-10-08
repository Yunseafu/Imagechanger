// usage: node tests/js_parity.mjs in.heic out.heic
import { readFileSync, writeFileSync } from "node:fs";
import { patch, looksLikeHeic, isStyled } from "../web/patcher.js";
const input = new Uint8Array(readFileSync(process.argv[2]));
if (!looksLikeHeic(input)) throw new Error("not heic");
const res = patch(input);
if (!isStyled(res.data)) throw new Error("output not recognised as styled");
try { patch(res.data); throw new Error("expected AlreadyStyled"); } catch (e) { if (e.name !== "Error" || e.message.startsWith("expected")) { if (e.message.startsWith("expected")) throw e; } }
writeFileSync(process.argv[3], res.data);
console.log(JSON.stringify(res.report));
