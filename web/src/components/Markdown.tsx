import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import bash from "highlight.js/lib/languages/bash";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import json from "highlight.js/lib/languages/json";
import sql from "highlight.js/lib/languages/sql";
import yaml from "highlight.js/lib/languages/yaml";
import xml from "highlight.js/lib/languages/xml";
import css from "highlight.js/lib/languages/css";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import rust from "highlight.js/lib/languages/rust";
import go from "highlight.js/lib/languages/go";
import java from "highlight.js/lib/languages/java";
import diff from "highlight.js/lib/languages/diff";
import markdown from "highlight.js/lib/languages/markdown";
import ini from "highlight.js/lib/languages/ini";
import { Icon } from "./Icon";
import { copyText } from "./copy";
import { t } from "../i18n";

const LANGS: Record<string, any> = { python, bash, javascript, typescript, json, sql, yaml, xml, css, c, cpp, rust, go, java, diff, markdown, ini };
for (const [k, v] of Object.entries(LANGS)) hljs.registerLanguage(k, v);
const ALIAS: Record<string, string> = { py: "python", sh: "bash", shell: "bash", zsh: "bash", js: "javascript", ts: "typescript", html: "xml", yml: "yaml", md: "markdown", toml: "ini", "c++": "cpp", rs: "rust", golang: "go", console: "bash", text: "", txt: "" };

/** Блок кода как в чат-ассистентах: подсветка, язык, «копировать». */
export function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const [done, setDone] = useState(false);
  const l = ALIAS[lang.toLowerCase()] ?? lang.toLowerCase();
  let html = "";
  try { html = l && hljs.getLanguage(l) ? hljs.highlight(code, { language: l, ignoreIllegals: true }).value : hljs.highlightAuto(code, Object.keys(LANGS)).value; }
  catch { html = code.replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
  return (
    <div className="codeblock">
      <div className="cb-bar"><span>{lang || t("code")}</span>
        <button className="cb-copy" onClick={async () => { await copyText(code); setDone(true); setTimeout(() => setDone(false), 1400); }} aria-label={t("Copy code")}>
          <Icon n={done ? "check" : "copy"} size={13} />{done ? t("Copied") : t("Copy")}</button></div>
      <pre><code className="hljs" dangerouslySetInnerHTML={{ __html: html }} /></pre>
    </div>
  );
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        pre: ({ children }) => <>{children}</>,
        code: ({ className, children }) => {
          const m = /language-([\w+#-]+)/.exec(className || "");
          const code = String(children).replace(/\n$/, "");
          if (m || code.includes("\n")) return <CodeBlock code={code} lang={m ? m[1] : ""} />;
          return <code>{children}</code>;
        },
        table: ({ children }) => <div className="md-table"><table>{children}</table></div>,
        a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a>,
      }}>{text}</ReactMarkdown>
    </div>
  );
}
