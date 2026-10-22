import { useEffect, useRef, useState } from "react";
import { del, get, post } from "../api";
import { useApp } from "../app";
import { Icon } from "../components/Icon";
import { fmtN } from "../components/ui";
import { t } from "../i18n";

interface Folder { id: number; name: string; description: string; file_count?: number; files?: number; size_bytes?: number }
interface RFile { id: number; filename: string; status: string; error?: string; size_bytes?: number; chunks?: number }
const statusRu: Record<string, string> = { queued: t("queued"), indexing: t("indexing"), ready: t("ready"), error: t("error"), cancelled: t("cancelled") };

export function Library() {
  const { toast } = useApp();
  const [folders, setFolders] = useState<Folder[]>([]);
  const [sel, setSel] = useState<number | null>(null);
  const [files, setFiles] = useState<RFile[]>([]);
  const [prov, setProv] = useState<any>(null);
  const [name, setName] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const loadFolders = () => get<Folder[]>("/api/rag/folders").then((f) => { setFolders(f); if (sel === null && f.length) setSel(f[0].id); });
  const loadFiles = () => sel !== null && get<RFile[]>(`/api/rag/folders/${sel}/files`).then(setFiles).catch(() => setFiles([]));
  useEffect(() => { document.title = t("Library"); loadFolders(); get("/api/rag/status").then(setProv).catch(() => {}); }, []);
  useEffect(() => { loadFiles(); const t = setInterval(loadFiles, 4000); return () => clearInterval(t); }, [sel]);
  const create = async () => { if (!name.trim()) return; try { const f = await post<Folder>("/api/rag/folders", { name }); setName(""); await loadFolders(); setSel(f.id); } catch (e: any) { toast(e.message); } };
  const upload = async (list: FileList | null) => {
    if (!list || sel === null) return; const form = new FormData(); Array.from(list).forEach((f) => form.append("files", f));
    try { const r = await fetch(`/api/rag/folders/${sel}/files`, { method: "POST", body: form, credentials: "same-origin" }); if (!r.ok) throw new Error((await r.json()).detail || r.statusText); toast(t("Files accepted, indexing started")); loadFiles(); } catch (e: any) { toast(e.message); }
  };
  const provText = prov?.provider ? (prov.provider.ok ? t("embeddings: {0}", prov.provider.model || "доступны") : t("embeddings unavailable: {0}", prov.provider.detail || prov.provider.error || "нет связи с Ollama")) : "";
  return (
    <div className="page scroll">
      <div className="page-in">
        <div className="row" style={{ marginBottom: 6 }}><h1 className="grow">{t("Library")}</h1>{provText && <span className={`pill ${prov?.provider?.ok ? "ok" : "warn"}`}>{provText}</span>}</div>
        <p className="muted" style={{ margin: "0 0 18px" }}>{t("Folders of documents the archivist searches by meaning. Attached to a symposium when it is created.")}</p>
        <div className="lib">
          <aside className="lib-side card">
            <div className="row" style={{ padding: 10 }}><input className="input" placeholder={t("New folder")} value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} /><button className="btn icon" onClick={create} aria-label={t("Create folder")}><Icon n="plus" /></button></div>
            {folders.map((f) => <button key={f.id} className={`side-item ${sel === f.id ? "on" : ""}`} onClick={() => setSel(f.id)}><Icon n="folder" size={15} /><span className="grow ellipsis">{f.name}</span></button>)}
            {folders.length === 0 && <div className="empty">{t("No folders")}</div>}
          </aside>
          <section className="lib-main card">
            {sel === null ? <div className="empty">{t("Create a folder to upload documents.")}</div> : (<>
              <div className="row" style={{ padding: 12 }}>
                <h3 className="grow">{folders.find((f) => f.id === sel)?.name}</h3>
                <button className="btn" onClick={() => input.current?.click()}><Icon n="upload" size={15} />{t("Upload files")}</button>
                <input ref={input} type="file" multiple hidden onChange={(e) => { upload(e.target.files); e.target.value = ""; }} />
                <button className="btn danger" onClick={async () => { if (confirm(t("Delete the folder with all its files?"))) { await del(`/api/rag/folders/${sel}`).catch((e) => toast(e.message)); setSel(null); loadFolders(); } }}><Icon n="trash" size={15} /></button>
              </div>
              {files.length === 0 ? <div className="empty">{t("No files yet. txt, md, pdf and docx are supported.")}</div> : files.map((f) => (
                <div className="file-row" key={f.id}>
                  <Icon n="file" size={15} /><span className="grow ellipsis">{f.filename}</span>
                  <span className={`pill ${f.status === "ready" ? "ok" : f.status === "error" ? "danger" : "accent"}`} title={f.error}>{statusRu[f.status] || f.status}</span>
                  {f.chunks != null && <span className="faint">{fmtN(f.chunks)} {t("chunks")}</span>}
                  <button className="btn ghost icon sm" onClick={async () => { await del(`/api/rag/files/${f.id}`).catch((e) => toast(e.message)); loadFiles(); }} aria-label={t("Delete file")}><Icon n="trash" size={14} /></button>
                </div>))}
            </>)}
          </section>
        </div>
      </div>
    </div>
  );
}
