import { useEffect, useState } from "react";
import { get, post, put } from "../../api";
import { useApp } from "../../app";
import { t } from "../../i18n";

interface P { key: string; content: string; source: "file" | "override"; is_default: boolean; default_content: string; active_version: number }
export function Prompts() {
  const { toast } = useApp();
  const [list, setList] = useState<P[]>([]); const [sel, setSel] = useState(""); const [text, setText] = useState("");
  const load = () => get<P[]>("/api/admin/prompts").then((l) => { setList(l); if (!sel && l.length) { setSel(l[0].key); setText(l[0].content); } }).catch((e) => toast(e.message));
  useEffect(() => { load(); }, []);
  const cur = list.find((p) => p.key === sel);
  const pick = (p: P) => { setSel(p.key); setText(p.content); };
  return (
    <div className="lib">
      <aside className="lib-side card scroll" style={{ maxHeight: "70vh" }}>{list.map((p) => <button key={p.key} className={`side-item ${sel === p.key ? "on" : ""}`} onClick={() => pick(p)}>
        <span className="grow ellipsis mono" style={{ fontSize: 13 }}>{p.key}</span>{p.source === "override" && <span className="pill warn">{t("edited")}</span>}</button>)}</aside>
      <section className="card pad">
        {cur ? <>
          <div className="row"><h3 className="grow mono">{cur.key}</h3><span className="pill">{cur.source === "override" ? t("edited v{0}", cur.active_version) : t("from the prompts/system file")}</span></div>
          <textarea className="textarea mono" style={{ minHeight: 360, margin: "10px 0" }} value={text} onChange={(e) => setText(e.target.value)} />
          <div className="row"><button className="btn primary" disabled={text.trim() === cur.content.trim()} onClick={async () => { try { await put(`/api/admin/prompts/${cur.key}`, { content: text }); toast(t("Saved")); load(); } catch (e: any) { toast(e.message); } }}>{t("Save override")}</button>
            <button className="btn" disabled={cur.source !== "override"} onClick={async () => { await post(`/api/admin/prompts/${cur.key}/reset`).catch((e) => toast(e.message)); setText(cur.default_content); load(); }}>{t("Restore from file")}</button></div>
          <p className="hint">{t("An override takes precedence over the file. Files in prompts/system are re-read from disk, so without an override simply edit them.")}</p></> : <div className="empty">{t("Choose a prompt")}</div>}
      </section>
    </div>
  );
}
