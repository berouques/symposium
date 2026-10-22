// One-off codemod: replaces Russian UI strings with t("English") calls (see i18n-extract.mjs).
import fs from "node:fs";
import path from "node:path";
import { findStrings, walk } from "./i18n-extract.mjs";

const tr = JSON.parse(fs.readFileSync("scripts/i18n-translations.json", "utf8"));
const SKIP_FILES = new Set(["AppSettings.tsx"]);
let changed = 0, missing = new Set();
for (const file of walk("src")) {
  if (SKIP_FILES.has(path.basename(file))) continue;
  let text = fs.readFileSync(file, "utf8");
  const hits = findStrings(file, text).sort((a, b) => b.start - a.start);
  if (!hits.length) continue;
  for (const h of hits) {
    const en = tr[h.key];
    if (en === undefined) { missing.add(h.key); continue; }
    const call = `t(${JSON.stringify(en)}${h.args.length ? ", " + h.args.join(", ") : ""})`;
    const repl = h.kind === "jsxtext" || h.kind === "attr" ? `{${call}}` : call;
    text = text.slice(0, h.start) + repl + text.slice(h.end);
    changed++;
  }
  if (!/from\s+["'][./]+\/i18n["']/.test(text)) {
    const rel = path.relative(path.dirname(file), "src/i18n").replace(/\\/g, "/");
    const imp = `import { t } from "${rel.startsWith(".") ? rel : "./" + rel}";\n`;
    const m = text.match(/^(?:import[^\n]*\n)+/);
    text = m ? text.slice(0, m[0].length) + imp + text.slice(m[0].length) : imp + text;
  }
  fs.writeFileSync(file, text);
}
console.log("replaced", changed, "missing", [...missing]);
