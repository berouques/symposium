import { useEffect, useRef, useState } from "react";
import { del, get, patch, post } from "../api";
import { useApp } from "../app";
import { t, getLang } from "../i18n";
import { AvatarParams, Character } from "../types";
import { Icon } from "../components/Icon";
import { Field, Modal } from "../components/ui";
import { RagPicker, TOOLS } from "../components/RagPicker";
import { TempPicker, ThinkingPicker, ceilingNote, tempLabel } from "../components/pickers";

export function CharAvatar({ c, size = 52 }: { c: Pick<Character, "id" | "avatar_seed" | "avatar_color">; size?: number }) {
  return <img className="avatar" style={{ width: size, height: size, borderRadius: size > 40 ? 12 : "50%", boxShadow: `0 0 0 2px ${c.avatar_color}` }} src={`/api/characters/${c.id}/avatar?v=${c.avatar_seed}-${Math.random().toString(36).slice(2, 6)}`} alt="" />;
}

interface Opts { hair: string[]; hair_color: string[]; face: string[]; bg: string[]; outfit: string[] }
const HAIR_NAME: Record<string, string> = { bald: "Bald", buzz: "Buzz cut", short: "Short", side: "Side part", long: "Long", curly: "Curly", bun: "Bun" };
const FACE_NAME: Record<string, string> = { none: "Clean", mustache: "Mustache", stubble: "Stubble", beard_short: "Short beard", beard_full: "Full beard", goatee: "Goatee",
  lipstick: "Lipstick", makeup_light: "Light makeup", makeup_bold: "Bold makeup" };

/** Four independent controls: hair, face features, background, outfit + frame. */
function AvatarEditor({ seed, value, onChange, onReroll }: { seed: string; value: AvatarParams; onChange: (v: AvatarParams) => void; onReroll?: () => void }) {
  const [opts, setOpts] = useState<Opts | null>(null);
  const [svg, setSvg] = useState("");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => { get<Opts>("/api/characters/avatar-options").then(setOpts).catch(() => {}); }, []);
  useEffect(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      const r = await fetch("/api/characters/preview", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ seed, avatar: value }) });
      if (r.ok) setSvg(await r.text());
    }, 80);
    return () => window.clearTimeout(timer.current);
  }, [seed, JSON.stringify(value)]);
  const set = (k: keyof AvatarParams, v: string) => onChange({ ...value, [k]: v });
  const sw = (k: keyof AvatarParams, list: string[]) => (
    <div className="swatches">{list.map((c) => <button key={c} type="button" className={`swatch ${value[k] === c ? "on" : ""}`} style={{ background: c }} onClick={() => set(k, c)} aria-label={c} />)}</div>);
  if (!opts) return null;
  return (
    <div className="avatar-editor">
      <div className="ae-preview"><div className="ae-img" dangerouslySetInnerHTML={{ __html: svg }} />{onReroll && <button type="button" className="btn sm" onClick={onReroll}><Icon n="refresh" size={13} />{t("Random look")}</button>}</div>
      <div className="ae-controls">
        <div><div className="label">{t("Hair")}</div><div className="chips">{opts.hair.map((h) => <button key={h} type="button" className={`chip ${value.hair === h ? "on" : ""}`} onClick={() => set("hair", h)}>{t(HAIR_NAME[h] || h)}</button>)}</div>{sw("hair_color", opts.hair_color)}</div>
        <div><div className="label">{t("Face (beard and mustache follow the hair colour)")}</div><div className="chips">{opts.face.map((f) => <button key={f} type="button" className={`chip ${value.face === f ? "on" : ""}`} onClick={() => set("face", f)}>{t(FACE_NAME[f] || f)}</button>)}</div></div>
        <div><div className="label">{t("Background")}</div>{sw("bg", opts.bg)}</div>
        <div><div className="label">{t("Outfit and frame")}</div>{sw("outfit", opts.outfit)}</div>
      </div>
    </div>
  );
}

function Editor({ c, onClose, onSaved }: { c: Character | null; onClose: () => void; onSaved: () => void }) {
  const { bootstrap, user, toast, refreshBootstrap } = useApp();
  const readOnly = !!c && !c.mine;
  const [seed, setSeed] = useState(c?.avatar_seed || Math.random().toString(16).slice(2, 18).padEnd(16, "0"));
  const [f, setF] = useState({ name: c?.name || "", title: c?.title || "", description: c?.description || "", prompt: c?.prompt || "", speech_style: c?.speech_style || "",
    temperature: c?.temperature ?? 0.7, max_tokens: c?.max_tokens ?? 350, thinking_mode: c?.thinking_mode ?? null, thinking_depth: c?.thinking_depth ?? null,
    api_profile_id: c?.api_profile_id ?? 0, profile_fallback: c?.profile_fallback ?? true, tools: c?.tools ?? ["ask_human"], rag_folder_ids: c?.rag_folder_ids ?? [],
    subagent_limit: c?.subagent_limit ?? 0, avatar: (c?.avatar ?? {}) as AvatarParams });
  const [cur, setCur] = useState(c);
  useEffect(() => { refreshBootstrap(); }, []);
  const set = (k: string, v: any) => setF((p) => ({ ...p, [k]: v }));
  const own = bootstrap.profiles.find((p) => p.id === f.api_profile_id) || null;
  const effective = own || bootstrap.profiles.find((p) => p.id === user.active_profile_id) || bootstrap.profiles[0] || null;
  const body = () => ({ ...f, api_profile_id: f.api_profile_id || null, thinking_mode: f.thinking_mode || "", thinking_depth: f.thinking_depth || "", subagent_limit: f.subagent_limit || null });
  const save = async () => {
    try { if (cur) await patch(`/api/characters/${cur.id}`, body()); else await post("/api/characters", body()); onSaved(); onClose(); } catch (e: any) { toast(e.message); }
  };
  const reroll = async () => { if (!cur) { setSeed(Math.random().toString(16).slice(2, 18).padEnd(16, "0")); set("avatar", {}); return; }
    try { const n = await post<Character>(`/api/characters/${cur.id}/avatar`); setCur(n); setSeed(n.avatar_seed); set("avatar", {}); } catch (e: any) { toast(e.message); } };
  return (
    <Modal wide title={c ? c.name : t("New character")} onClose={onClose}
      footer={<>{cur && <button className="btn" onClick={async () => { const n = await post<Character>(`/api/characters/${cur.id}/clone`); onSaved(); onClose(); toast(t("Created «{0}» — find it among your characters", n.name)); }}><Icon n="copy" size={14} />{t("Create based on this")}</button>}
        {cur && !readOnly && <button className="btn danger" onClick={async () => { if (confirm(t("Delete the character? Past symposia are not affected."))) { await del(`/api/characters/${cur.id}`).catch((e) => toast(e.message)); onSaved(); onClose(); } }}><Icon n="trash" size={14} /></button>}
        <span className="spacer" /><button className="btn" onClick={onClose}>{t("Close")}</button>{!readOnly && <button className="btn primary" disabled={!f.name.trim()} onClick={save}>{t("Save")}</button>}</>}>
      {readOnly && <div className="pill warn">{t("Built-in character: edit its file in prompts/characters/, or press «Create based on this» to make your own copy.")}</div>}
      <fieldset disabled={readOnly} className="fs">
        <AvatarEditor seed={seed} value={f.avatar} onChange={(v) => set("avatar", v)} onReroll={readOnly ? undefined : reroll} />
        <div className="grid2">
          <Field label={t("Name")}><input className="input" value={f.name} onChange={(e) => set("name", e.target.value)} /></Field>
          <Field label={t("Title")} hint={t("Short occupation shown next to the name, e.g. «Art critic».")}><input className="input" value={f.title} onChange={(e) => set("title", e.target.value)} /></Field>
        </div>
        <Field label={t("Description")} hint={t("How the character is introduced to the other participants.")}><textarea className="textarea" style={{ minHeight: 56 }} value={f.description} onChange={(e) => set("description", e.target.value)} /></Field>
        <Field label={t("Persona prompt")} hint={t("Who they are, how they reason and argue. This is the character's core instruction.")}><textarea className="textarea" style={{ minHeight: 150 }} value={f.prompt} onChange={(e) => set("prompt", e.target.value)} /></Field>
        <Field label={t("Manner of speech")} hint={t("Briefly: formal, vivid, terse…")}><input className="input" value={f.speech_style} onChange={(e) => set("speech_style", e.target.value)} /></Field>
        <Field label={t("Temperature — manner of speech")}><TempPicker value={f.temperature} onChange={(v) => set("temperature", v)} /></Field>
        <div className="grid2">
          <Field label={t("Visible reply budget, tokens")} hint={t("Reasoning is counted separately.")}><input className="input" type="number" min={64} value={f.max_tokens} onChange={(e) => set("max_tokens", Number(e.target.value))} /></Field>
          <Field label={t("Subagent calls per turn")} hint={t("0 — use the global setting.")}><input className="input" type="number" min={0} value={f.subagent_limit} onChange={(e) => set("subagent_limit", Number(e.target.value))} /></Field>
        </div>
        <Field label={t("Reasoning")} hint={ceilingNote(effective)}><ThinkingPicker mode={f.thinking_mode} depth={f.thinking_depth} profile={effective} onChange={(m, d) => setF((p) => ({ ...p, thinking_mode: m, thinking_depth: d }))} /></Field>
        <div className="profile-box">
          <Field label={t("LLM profile")}>
            <select className="select" value={f.api_profile_id} onChange={(e) => set("api_profile_id", Number(e.target.value))}>
              <option value={0}>{t("Use the symposium's profile")}</option>
              {bootstrap.profiles.map((p) => <option key={p.id} value={p.id}>{p.name} — {p.model}</option>)}</select></Field>
          {f.api_profile_id !== 0 && (
            <label className="row" style={{ gap: 8, marginTop: 6 }}><input type="checkbox" checked={f.profile_fallback} onChange={(e) => set("profile_fallback", e.target.checked)} />
              <span>{t("Fall back to the symposium's profile if this one is unavailable")}</span></label>)}
          <p className="hint" style={{ margin: "6px 0 0" }}>{f.api_profile_id === 0 ? t("The character speaks with whatever model the symposium uses.")
            : f.profile_fallback ? t("If this profile fails or is deleted, the character continues on the symposium's profile (the reply is marked).")
              : t("If this profile fails or is deleted, the symposium STOPS and tells you to open the participant's settings.")}</p>
        </div>
        <Field label={t("Permitted tools")} hint={t("Default: only questions to the human.")}><div className="tool-chips">{TOOLS.map(([k, l]) => (
          <label key={k} className={`chip ${f.tools.includes(k) ? "on" : ""}`}><input type="checkbox" className="sr" checked={f.tools.includes(k)} onChange={(e) => set("tools", e.target.checked ? [...f.tools, k] : f.tools.filter((x) => x !== k))} />{t(l)}</label>))}</div></Field>
        <Field label={t("Private library folders")} hint={t("Available only to this character (besides the symposium's own folders).")}><RagPicker selected={f.rag_folder_ids} allowUpload onToggle={(id, on) => set("rag_folder_ids", on ? [...f.rag_folder_ids, id] : f.rag_folder_ids.filter((x) => x !== id))} /></Field>
      </fieldset>
    </Modal>
  );
}

export function Characters() {
  const [list, setList] = useState<Character[] | null>(null);
  const [edit, setEdit] = useState<Character | null | "new">(null);
  const load = () => get<Character[]>(`/api/characters?lang=${getLang()}`).then(setList).catch(() => setList([]));
  useEffect(() => { document.title = t("Characters"); load(); }, []);
  const mine = (list || []).filter((c) => !c.is_default);
  const builtin = (list || []).filter((c) => c.is_default);
  const card = (c: Character) => (
    <button key={c.id} className="char-card" onClick={() => setEdit(c)}>
      <div className="char-top"><CharAvatar c={c} /><div className="grow" style={{ minWidth: 0 }}><div className="char-name ellipsis">{c.name}</div><div className="faint ellipsis">{c.title}</div></div></div>
      <div className="char-desc">{c.description || <span className="faint">{t("no description")}</span>}</div>
      <div className="tool-chips"><span className="tag">{tempLabel(c.temperature)}</span>{c.tools && c.tools.length > 1 && <span className="tag">{t("tools: {0}", c.tools.length)}</span>}{c.api_profile_id && <span className="tag">{t("own profile")}</span>}{c.rag_folder_ids.length > 0 && <span className="tag">{t("folders: {0}", c.rag_folder_ids.length)}</span>}</div>
    </button>);
  return (
    <div className="page scroll"><div className="page-in">
      <div className="row" style={{ marginBottom: 6 }}><h1 className="grow">{t("Characters")}</h1><button className="btn primary" onClick={() => setEdit("new")}><Icon n="plus" size={15} />{t("New character")}</button></div>
      <p className="muted" style={{ margin: "0 0 18px" }}>{t("Ready-made participants: portrait, persona, manner, model, tools and private folders. You pick characters when creating a symposium — their settings are copied, later edits never change past symposia.")}</p>
      {list === null ? <div className="empty">{t("Loading…")}</div> : <>
        <h3 style={{ margin: "0 0 10px" }}>{t("My characters")}</h3>
        {mine.length === 0 ? <div className="empty card" style={{ marginBottom: 22 }}>{t("None yet. Create one, or open a built-in character and press «Create based on this».")}</div> : <div className="char-grid" style={{ marginBottom: 24 }}>{mine.map(card)}</div>}
        <h3 style={{ margin: "0 0 4px" }}>{t("Built-in")}</h3><p className="hint" style={{ margin: "0 0 10px" }}>{t("Defined in prompts/characters/*.json — edit the files, changes are picked up live.")}</p><div className="char-grid">{builtin.map(card)}</div></>}
      {edit && <Editor c={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={load} />}
    </div></div>
  );
}
