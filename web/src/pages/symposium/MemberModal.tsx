import { useEffect, useState } from "react";
import { patch } from "../../api";
import { useApp } from "../../app";
import { t } from "../../i18n";
import { Detail, Member } from "../../types";
import { Field, Modal } from "../../components/ui";
import { TempPicker, ThinkingPicker, ceilingNote } from "../../components/pickers";
import { RagPicker, TOOLS } from "../../components/RagPicker";

/** Per-participant settings, editable while the symposium runs. */
export function MemberModal({ detail, m, onClose, onSaved }: { detail: Detail; m: Member; onClose: () => void; onSaved: () => void }) {
  const { bootstrap, toast, refreshBootstrap } = useApp();
  useEffect(() => { refreshBootstrap(); }, []);
  const own = bootstrap.profiles.find((p) => p.id === (m.api_profile_id ?? 0)) || null;
  const symProfile = bootstrap.profiles.find((p) => p.id === detail.api_profile?.id) || null;
  const [f, setF] = useState({ enabled: m.enabled, temperature: m.temperature, max_tokens: m.max_tokens, user_prompt: m.user_prompt,
    thinking_mode: m.thinking_mode, thinking_depth: m.thinking_depth, api_profile_id: m.api_profile_id ?? 0, profile_fallback: m.profile_fallback,
    tools: m.tools ?? TOOLS.map((x) => x[0]), rag_folder_ids: m.rag_folder_ids || [] });
  const effective = bootstrap.profiles.find((p) => p.id === f.api_profile_id) || own || symProfile;
  const save = async () => {
    try {
      await patch(`/api/symposia/${detail.id}/members/${m.id}`, { ...f, tools: f.tools.length < TOOLS.length ? f.tools : ["*"], thinking_mode: f.thinking_mode || "", thinking_depth: f.thinking_depth || "" });
      onSaved(); onClose();
    } catch (e: any) { toast(e.message); }
  };
  return (
    <Modal title={`${m.name}${m.role ? ` — ${m.role}` : ""}`} onClose={onClose} footer={<><button className="btn" onClick={onClose}>{t("Cancel")}</button><button className="btn primary" onClick={save}>{t("Save")}</button></>}>
      <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} />
        <b>{t("Takes part in the symposium")}</b><span className="hint">{t("A disabled participant neither speaks nor votes; earlier replies stay in the history.")}</span></label>
      <div className="profile-box">
        <Field label={t("LLM profile")}>
          <select className="select" value={f.api_profile_id} onChange={(e) => setF({ ...f, api_profile_id: Number(e.target.value) })}>
            <option value={0}>{t("Use the symposium's profile")}{symProfile ? ` (${symProfile.name})` : ""}</option>
            {bootstrap.profiles.map((p) => <option key={p.id} value={p.id}>{p.name} — {p.model}</option>)}</select></Field>
        {f.api_profile_id !== 0 && <label className="row" style={{ gap: 8, marginTop: 6 }}><input type="checkbox" checked={f.profile_fallback} onChange={(e) => setF({ ...f, profile_fallback: e.target.checked })} />
          <span>{t("Fall back to the symposium's profile if this one is unavailable")}</span></label>}
        <p className="hint" style={{ margin: "6px 0 0" }}>{f.api_profile_id === 0 ? t("The participant speaks with whatever model the symposium uses.")
          : f.profile_fallback ? t("If this profile fails or is deleted, the participant continues on the symposium's profile (the reply is marked).")
            : t("If this profile fails or is deleted, the symposium STOPS and tells you to open the participant's settings.")}</p>
      </div>
      <Field label={t("Temperature — manner of speech")}><TempPicker value={f.temperature} onChange={(v) => setF({ ...f, temperature: v })} /></Field>
      <Field label={t("Reasoning")} hint={ceilingNote(effective)}>
        <ThinkingPicker mode={f.thinking_mode} depth={f.thinking_depth} profile={effective} onChange={(mode, depth) => setF({ ...f, thinking_mode: mode, thinking_depth: depth })} /></Field>
      <Field label={t("Permitted tools")}><div className="tool-chips">{TOOLS.map(([k, l]) => (
        <label key={k} className={`chip ${f.tools.includes(k) ? "on" : ""}`}><input type="checkbox" className="sr" checked={f.tools.includes(k)} onChange={(e) => setF({ ...f, tools: e.target.checked ? [...f.tools, k] : f.tools.filter((x) => x !== k) })} />{t(l)}</label>))}</div></Field>
      <Field label={t("Private library folders")} hint={t("Available only to this participant.")}><RagPicker selected={f.rag_folder_ids} onToggle={(id, on) => setF({ ...f, rag_folder_ids: on ? [...f.rag_folder_ids, id] : f.rag_folder_ids.filter((x) => x !== id) })} /></Field>
      <Field label={t("Visible reply budget, tokens")} hint={t("Reasoning is counted separately.")}>
        <input className="input" type="number" min={64} value={f.max_tokens} onChange={(e) => setF({ ...f, max_tokens: Number(e.target.value) })} /></Field>
      <Field label={t("Additional instruction for this symposium")}><textarea className="textarea" value={f.user_prompt} onChange={(e) => setF({ ...f, user_prompt: e.target.value })} /></Field>
    </Modal>
  );
}
