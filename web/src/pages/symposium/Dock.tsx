import { useEffect, useState } from "react";
import { get, post } from "../../api";
import { useApp } from "../../app";
import { Detail, Evidence, Message, Round } from "../../types";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { fmtN } from "../../components/ui";
import { Terminal } from "./Terminal";
import { RagPicker } from "../../components/RagPicker";
import { UsageTab } from "./Usage";
import { t } from "../../i18n";

type Tab = "workspace" | "terminal" | "rag" | "evidence" | "digest" | "usage" | "summary";
const TABS: [Tab, string, string][] = [["workspace", t("Files"), "folder"], ["terminal", t("Terminal"), "terminal"], ["rag", t("Library"), "book"], ["evidence", t("Evidence"), "layers"], ["digest", t("Digest"), "quote"], ["usage", t("Usage"), "cpu"], ["summary", t("Summary"), "file"]];
const AGENT_NAME: Record<string, string> = { web_explorer: t("Web search"), archivarius: t("Archivist"), coder: t("Coder"), ask_human: t("Human") };
const WS_STATE: Record<string, string> = { running: t("running"), starting: t("starting"), stopped: t("stopped"), none: t("not created"), error: t("error") };

interface Node { name: string; path: string; type: "dir" | "file"; size?: number; children?: Node[] }

function Tree({ nodes, onOpen, sel, depth = 0 }: { nodes: Node[]; onOpen: (p: string) => void; sel: string; depth?: number }) {
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  return <>{nodes.map((n) => n.type === "dir" ? (
    <div key={n.path}><button className="tn" style={{ paddingLeft: 8 + depth * 14 }} onClick={() => setClosed({ ...closed, [n.path]: !closed[n.path] })}>
      <Icon n={closed[n.path] ? "chevr" : "chev"} size={12} /><Icon n="folder" size={14} />{n.name}</button>
      {!closed[n.path] && <Tree nodes={n.children || []} onOpen={onOpen} sel={sel} depth={depth + 1} />}</div>
  ) : (
    <button key={n.path} className={`tn ${sel === n.path ? "on" : ""}`} style={{ paddingLeft: 8 + depth * 14 + 14 }} onClick={() => onOpen(n.path)}>
      <Icon n="file" size={14} /><span className="grow ellipsis">{n.name}</span><span className="faint">{fmtN(n.size || 0)}</span></button>
  ))}</>;
}

function Workspace({ detail, active }: { detail: Detail; active: boolean }) {
  const { toast } = useApp();
  const [info, setInfo] = useState<{ state: string; reason: string; tree: Node[] } | null>(null);
  const [file, setFile] = useState<{ path: string; content: string | null; binary: boolean; size: number } | null>(null);
  const load = () => get(`/api/symposia/${detail.id}/workspace`).then(setInfo).catch(() => {});
  useEffect(() => { if (!active) return; load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [active, detail.id]);
  const open = (p: string) => get(`/api/symposia/${detail.id}/workspace/file?path=${encodeURIComponent(p)}`).then(setFile).catch((e) => toast(e.message));
  const act = (a: string) => post(`/api/symposia/${detail.id}/workspace/${a}`).then(load).catch((e) => toast(e.message));
  const st = info?.state || detail.ws_state;
  return (
    <div className="ws">
      <div className="row dock-bar"><span className={`pill ${st === "running" ? "ok" : st === "error" ? "danger" : ""}`}>{WS_STATE[st] || st}</span>
        {info?.reason && info.reason !== "local_mode" && <span className="faint ellipsis grow" title={info.reason}>{info.reason}</span>}<span className="spacer" />
        <button className="btn sm" onClick={() => act("wake")}>{t("Start")}</button>
        <button className="btn sm ghost icon" onClick={load} aria-label={t("Refresh")}><Icon n="refresh" size={14} /></button></div>
      <div className="ws-body">
        <div className="tree scroll">{info?.tree?.length ? <Tree nodes={info.tree} onOpen={open} sel={file?.path || ""} /> : <div className="empty">{t("No files yet. Participants create them through the coder.")}</div>}</div>
        {file && (
          <div className="viewer scroll">
            <div className="row dock-bar"><b className="ellipsis grow mono">{file.path}</b>
              <a className="btn sm" href={`/api/symposia/${detail.id}/workspace/download?path=${encodeURIComponent(file.path)}`}><Icon n="download" size={13} /></a>
              <button className="btn sm ghost icon" onClick={() => setFile(null)} aria-label={t("Close")}><Icon n="x" size={13} /></button></div>
            {file.binary ? <div className="empty">{t("Binary file — download it.")}</div> : /\.md$/i.test(file.path) ? <Markdown text={file.content || ""} /> : <pre className="code">{file.content}</pre>}
          </div>
        )}
      </div>
    </div>
  );
}

function Rag({ detail, onChange }: { detail: Detail; onChange: () => void }) {
  const { toast } = useApp();
  const attached = detail.rag_folders.map((f) => f.id);
  const toggle = (id: number, on: boolean) => post(`/api/symposia/${detail.id}/rag-folders`, { folder_id: id, action: on ? "attach" : "detach" }).then(onChange).catch((e) => toast(e.message));
  return (
    <div className="pad">
      <p className="muted" style={{ marginTop: 0 }}>{t("Attach library folders — the archivist can search them by meaning. Takes effect from the next turn; you can create a folder and upload files right here.")}</p>
      <RagPicker selected={attached} onToggle={toggle} />
      <p style={{ marginTop: 16 }}><a href="#/library">{t("Open the library →")}</a></p>
    </div>
  );
}

function DigestTab({ rounds, detail }: { rounds: Round[]; detail: Detail }) {
  const done = rounds.filter((r) => r.summary);
  return (
    <div className="pad stack">
      {detail.checklist.length > 0 && <div className="card pad"><b>{t("Checklist")}</b>{detail.checklist.map((c) => <div key={c.ref} className={`ck ${c.status}`}><span className="ck-box">{c.status === "done" && <Icon n="check" size={12} />}</span><span><b>{c.ref}</b> {c.text}</span></div>)}</div>}
      {done.length === 0 ? <div className="empty">{t("Digests will appear after the first completed round.")}</div> : done.map((r) => <div key={r.id}><div className="faint" style={{ fontSize: 12, marginBottom: 2 }}>{t("Round")} {r.number}</div><pre className="digest">{r.summary!.replace(/^Round \d+:\n?/, "")}</pre></div>)}
    </div>
  );
}

function EvidenceTab({ evidence, detail }: { evidence: Evidence[]; detail: Detail }) {
  const names = Object.fromEntries(detail.members.map((m) => [m.id, m.name]));
  if (!evidence.length) return <div className="empty">{t("No evidence yet. When participants turn to web search, the library or the coder, the results appear here — shared by the whole table.")}</div>;
  return <div className="pad stack">{[...evidence].reverse().map((e) => (
    <div className={`ev-card ${e.ok ? "" : "bad"}`} key={e.ref}><div className="row"><b>{e.ref}</b><span className="muted">{AGENT_NAME[e.agent] || e.agent}</span><span className="faint grow ellipsis">{names[e.member_id ?? -1]}: {e.query}</span></div>
      <div className="ev-text"><Markdown text={e.digest} /></div></div>))}</div>;
}

function Summary({ detail, rounds, messages }: { detail: Detail; rounds: Round[]; messages: Message[] }) {
  const { toast } = useApp();
  const report = [...messages].reverse().find((m) => m.kind === "report");
  const exp = async (format: string, filter: string) => {
    const r = await fetch(`/api/symposia/${detail.id}/export`, { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify({ format, filter }) });
    if (!r.ok) return toast(t("Could not export"));
    const cd = r.headers.get("content-disposition") || ""; const m = cd.match(/filename\*=UTF-8''([^;]+)/);
    const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = m ? decodeURIComponent(m[1]) : `symposium_${detail.id}.${format}`; a.click();
  };
  return (
    <div className="pad stack">
      <div className="row wrap"><button className="btn sm" onClick={() => exp("md", "discussion")}><Icon n="download" size={13} />{t("Discussion .md")}</button>
        <button className="btn sm" onClick={() => exp("md", "full")}>{t("Full .md")}</button><button className="btn sm" onClick={() => exp("json", "full")}>JSON</button>
        <button className="btn sm" title={t("All tables, reasoning, <state> blocks, prompts, tokens, subagent logs")} onClick={() => exp("dump", "full")}><Icon n="layers" size={13} />{t("Full dump (zip)")}</button></div>
      {report ? <div className="card pad"><Markdown text={report.content} /></div> : <div className="faint">{t("The final report will appear when the symposium ends.")}</div>}
      <h4 style={{ margin: "8px 0 0" }}>{t("Round digests")}</h4>
      {rounds.filter((r) => r.summary).map((r) => <details key={r.id} className="digest-d"><summary>{t("Round")} {r.number}</summary><pre className="digest">{r.summary}</pre></details>)}
    </div>
  );
}

export function Dock({ detail, rounds, messages, evidence, onChange }: { detail: Detail; rounds: Round[]; messages: Message[]; evidence: Evidence[]; onChange: () => void }) {
  const [tab, setTab] = useState<Tab>((localStorage.getItem("dockTab") as Tab) || "workspace");
  const [visited, setVisited] = useState<Set<Tab>>(new Set([tab]));
  const pick = (t: Tab) => { setTab(t); localStorage.setItem("dockTab", t); setVisited((v) => new Set(v).add(t)); };
  return (
    <aside className="dock">
      <div className="tabs" role="tablist">{TABS.map(([k, label, icon]) => (
        <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => pick(k)}><Icon n={icon} size={14} /><span>{label}</span>
          {k === "evidence" && evidence.length > 0 && <span className="count">{evidence.length}</span>}</button>))}</div>
      <div className="dock-body">
        <div className="pane" hidden={tab !== "workspace"}>{visited.has("workspace") && <Workspace detail={detail} active={tab === "workspace"} />}</div>
        <div className="pane" hidden={tab !== "terminal"}>{visited.has("terminal") && <Terminal sid={detail.id} active={tab === "terminal"} />}</div>
        <div className="pane scroll" hidden={tab !== "rag"}><Rag detail={detail} onChange={onChange} /></div>
        <div className="pane scroll" hidden={tab !== "evidence"}><EvidenceTab evidence={evidence} detail={detail} /></div>
        <div className="pane scroll" hidden={tab !== "digest"}><DigestTab rounds={rounds} detail={detail} /></div>
        <div className="pane scroll" hidden={tab !== "usage"}>{visited.has("usage") && <UsageTab detail={detail} />}</div>
        <div className="pane scroll" hidden={tab !== "summary"}><Summary detail={detail} rounds={rounds} messages={messages} /></div>
      </div>
    </aside>
  );
}
