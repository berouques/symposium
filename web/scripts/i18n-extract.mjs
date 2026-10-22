// Finds every Cyrillic UI string in src/**/*.tsx (JSX text, attributes, string/template literals)
// and writes scripts/i18n-keys.json: {key: [files]} where key is the Russian text with {0},{1} placeholders.
import ts from "typescript";
import fs from "node:fs";
import path from "node:path";

const SKIP = new Set(["i18n.ts", "ru.ts"]);
const CYR = /[А-Яа-яЁё]/;
export function walk(dir, out = []) {
  for (const f of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, f.name);
    if (f.isDirectory()) walk(p, out); else if (/\.tsx?$/.test(f.name) && !SKIP.has(f.name)) out.push(p);
  }
  return out;
}
const norm = (s) => s.replace(/\s+/g, " ").trim();

/** Returns [{start,end,kind,key,args:[exprText], jsx:boolean}] for one source file. */
export function findStrings(file, text) {
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const hits = [];
  const visit = (n) => {
    if (ts.isImportDeclaration(n)) return;
    if (ts.isJsxText(n)) {
      const raw = n.getText(sf);
      if (CYR.test(raw) && raw.trim()) {
        const lead = raw.length - raw.trimStart().length, trail = raw.length - raw.trimEnd().length;
        hits.push({ start: n.getStart(sf) + lead, end: n.getEnd() - trail, kind: "jsxtext", key: norm(raw), args: [] });
      }
      return;
    }
    if ((ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) && CYR.test(n.text)) {
      const attr = ts.isJsxAttribute(n.parent) && n.parent.initializer === n;
      hits.push({ start: n.getStart(sf), end: n.getEnd(), kind: attr ? "attr" : "lit", key: n.text, args: [] });
      return;
    }
    if (ts.isTemplateExpression(n)) {
      const whole = n.getText(sf);
      if (CYR.test(whole)) {
        let key = n.head.text; const args = [];
        n.templateSpans.forEach((sp, i) => { key += `{${i}}` + sp.literal.text; args.push(sp.expression.getText(sf)); });
        if (CYR.test(key)) { hits.push({ start: n.getStart(sf), end: n.getEnd(), kind: "tpl", key, args }); return; }
      }
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
  return hits;
}

if (process.argv[1].endsWith("i18n-extract.mjs")) {
  const keys = {};
  for (const f of walk("src")) {
    for (const h of findStrings(f, fs.readFileSync(f, "utf8"))) (keys[h.key] ||= new Set()).add(f.replace("src/", ""));
  }
  const out = Object.fromEntries(Object.entries(keys).map(([k, v]) => [k, [...v]]));
  fs.writeFileSync("scripts/i18n-keys.json", JSON.stringify(out, null, 1));
  console.log(Object.keys(out).length, "unique strings");
}
