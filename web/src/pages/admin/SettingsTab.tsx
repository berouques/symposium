import { useEffect, useState } from "react";
import { get, patch } from "../../api";
import { useApp } from "../../app";
import { Field } from "../../components/ui";
import { t } from "../../i18n";

type Def = [key: string, label: string, hint?: string, kind?: "bool" | "int" | "list" | "select:digest|llm" | "select:docker|local"];
const GROUPS: [string, Def[]][] = [
  [t("Web search (SearXNG)"), [["web_search_enabled", t("Web search enabled"), "", "bool"], ["web_search_searxng_url", t("SearXNG address"), t("For example http://localhost:8080; the JSON format must be enabled.")],
    ["web_search_engines", t("Search engines (JSON array)"), t("For example [\"searxng\",\"google\",\"bing\",\"yandex\"]"), "list"], ["web_search_max_results", t("Results per query"), "", "int"], ["web_search_timeout_sec", t("Timeout, s"), "", "int"]]],
  [t("Library (Ollama)"), [["ollama_url", t("Ollama address"), t("Empty — taken from .env (SYMPOSIUM_OLLAMA_URL).")], ["embedding_model", t("Embedding model"), t("For example nomic-embed-text")],
    ["embedding_dims", t("Dimension"), "", "int"], ["rag_chunk_chars", t("Chunk size, chars"), "", "int"], ["rag_chunk_overlap", t("Chunk overlap, chars"), "", "int"]]],
  [t("Code execution and files"), [["sandbox_max_timeout_sec", t("Maximum command timeout, s"), t("The coder runs code in the symposium's workspace (each has its own Python virtual environment; pip install works)."), "int"],
    ["workspace_mem_limit_mb", t("Command memory limit, MB"), "", "int"], ["web_fetch_max_bytes", t("Maximum download size, bytes"), t("Downloading files from the internet for analysis."), "int"],
    ["web_fetch_allow_private", t("Allow downloads from local addresses"), t("Off: participants cannot reach Ollama, SearXNG and other local services."), "bool"]]],
  [t("Symposium flow"), [["strict_state", t("Require the state block"), t("No block → one request, then the reply is accepted with a mark."), "bool"],
    ["summary_mode", t("Round digest"), t("digest — no LLM call (saves requests); llm — the secretary."), "select:digest|llm"], ["stall_rounds", t("Rounds without new claims before a completion request"), t("0 — off."), "int"],
    ["default_max_rounds", t("Default round limit"), t("0 — no limit."), "int"], ["compression_threshold", t("Rounds kept verbatim in context"), t("Older ones — as a digest."), "int"],
    ["subagent_calls_per_turn", t("Subagent calls per turn"), "", "int"], ["subagent_digest_tokens", t("Subagent digest size, tokens"), "", "int"], ["vote_max_tokens", t("Tokens per ballot"), "", "int"]]],
];

export function SettingsTab() {
  const { toast } = useApp();
  const [s, setS] = useState<Record<string, any> | null>(null);
  const [dirty, setDirty] = useState<Record<string, any>>({});
  useEffect(() => { get("/api/admin/settings").then(setS).catch((e) => toast(e.message)); }, []);
  if (!s) return <div className="empty">{t("Loading…")}</div>;
  const val = (k: string) => (k in dirty ? dirty[k] : s[k]);
  const set = (k: string, v: any) => setDirty({ ...dirty, [k]: v });
  const save = async () => {
    const body: Record<string, any> = {};
    for (const [k, v] of Object.entries(dirty)) { try { body[k] = typeof s[k] === "object" && typeof v === "string" ? JSON.parse(v) : v; } catch { return toast(t("Field {0}: invalid JSON", k)); } }
    try { await patch("/api/admin/settings", body); setS({ ...s, ...body }); setDirty({}); toast(t("Settings saved")); } catch (e: any) { toast(e.message); }
  };
  const known = new Set(GROUPS.flatMap(([, d]) => d.map((x) => x[0])));
  const input = ([k, label, hint, kind]: Def) => {
    if (!(k in s)) return null; const v = val(k);
    return (
      <Field key={k} label={label} hint={hint}>
        {kind === "bool" ? <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={!!v} onChange={(e) => set(k, e.target.checked)} />{t("On")}</label>
          : kind?.startsWith("select") ? <select className="select" value={String(v)} onChange={(e) => set(k, e.target.value)}>{kind.split(":")[1].split("|").map((o) => <option key={o}>{o}</option>)}</select>
          : kind === "int" ? <input className="input" type="number" value={v} onChange={(e) => set(k, Number(e.target.value))} />
          : kind === "list" ? <input className="input mono" value={typeof v === "string" ? v : JSON.stringify(v)} onChange={(e) => set(k, e.target.value)} />
          : <input className="input" value={v ?? ""} onChange={(e) => set(k, e.target.value)} />}
      </Field>);
  };
  const rest = Object.keys(s).filter((k) => !known.has(k)).sort();
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}><p className="muted grow" style={{ margin: 0 }}>{t("Values from .env are only starting defaults; here you change the live settings.")}</p>
        <button className="btn primary" disabled={!Object.keys(dirty).length} onClick={save}>{t("Save")}{Object.keys(dirty).length ? ` (${Object.keys(dirty).length})` : ""}</button></div>
      {GROUPS.map(([title, defs]) => <section className="form-sec card pad" key={title}><h3>{title}</h3><div className="grid2">{defs.map(input)}</div></section>)}
      {rest.length > 0 && <details className="form-sec card pad"><summary><b>{t("Advanced")}</b> <span className="faint">({rest.length})</span></summary>
        <div className="grid2" style={{ marginTop: 10 }}>{rest.map((k) => input([k, k, "", typeof s[k] === "boolean" ? "bool" : typeof s[k] === "number" ? "int" : Array.isArray(s[k]) ? "list" : undefined]))}</div></details>}
    </>
  );
}
