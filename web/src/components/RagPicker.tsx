import { useEffect, useRef, useState } from "react";
import { get, post } from "../api";
import { useApp } from "../app";
import { RagFolder } from "../types";
import { Icon } from "./Icon";
import { t } from "../i18n";

export const TOOLS: [string, string][] = [["web_explorer", t("Web search")], ["web_fetch", t("File download")], ["archivarius", t("Library (RAG)")], ["coder", t("Coder (python/shell)")], ["ask_human", t("Questions to the human")]];

/** Выбор папок библиотеки RAG: свежий список, создание папки и загрузка файлов «на месте». */
export function RagPicker({ selected, onToggle, allowUpload = true }: { selected: number[]; onToggle: (id: number, on: boolean) => void; allowUpload?: boolean }) {
  const { toast } = useApp();
  const [folders, setFolders] = useState<RagFolder[]>([]);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const [target, setTarget] = useState<number | null>(null);
  const load = () => get<RagFolder[]>("/api/rag/folders").then(setFolders).catch(() => {});
  useEffect(() => { load(); }, []);
  const create = async () => {
    if (!name.trim()) return;
    try { const f = await post<RagFolder>("/api/rag/folders", { name }); setName(""); await load(); onToggle(f.id, true); } catch (e: any) { toast(e.message); }
  };
  const upload = async (list: FileList | null) => {
    if (!list || target === null) return;
    const form = new FormData(); Array.from(list).forEach((f) => form.append("files", f)); setBusy(target);
    try {
      const r = await fetch(`/api/rag/folders/${target}/files`, { method: "POST", body: form, credentials: "same-origin" });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      toast(t("Files accepted, indexing started")); if (!selected.includes(target)) onToggle(target, true);
    } catch (e: any) { toast(e.message); } finally { setBusy(null); }
  };
  return (
    <div className="rag-picker">
      {folders.length === 0 && <div className="hint">{t("No folders yet — create the first one below.")}</div>}
      <div className="chips">{folders.map((f) => {
        const on = selected.includes(f.id);
        return (
          <span key={f.id} className={`chip ${on ? "on" : ""}`}>
            <label className="row" style={{ gap: 6, cursor: "pointer" }}><input type="checkbox" className="sr" checked={on} onChange={(e) => onToggle(f.id, e.target.checked)} /><Icon n="folder" size={14} />{f.name}</label>
            {allowUpload && <button className="chip-btn" title={t("Upload files to this folder")} aria-label={t("Upload files to {0}", f.name)} disabled={busy === f.id} onClick={() => { setTarget(f.id); setTimeout(() => input.current?.click(), 0); }}><Icon n="upload" size={13} /></button>}
          </span>);
      })}</div>
      <div className="row" style={{ marginTop: 8 }}>
        <input className="input" placeholder={t("New folder")} value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} />
        <button className="btn" onClick={create} disabled={!name.trim()}><Icon n="plus" size={14} />{t("Create")}</button>
      </div>
      <input ref={input} type="file" multiple hidden onChange={(e) => { upload(e.target.files); e.target.value = ""; }} />
    </div>
  );
}
