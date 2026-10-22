import { useEffect, useState } from "react";
import { get, post } from "../api";
import { go, useApp } from "../app";
import { Character } from "../types";
import { Icon } from "../components/Icon";
import { Field } from "../components/ui";
import { getLang, t } from "../i18n";
import { RagPicker } from "../components/RagPicker";
import { ceilingNote, tempLabel } from "../components/pickers";
import { CharAvatar } from "./Characters";

const FORM_INFO: Record<string, string> = {
  free: t("Free form — participants shape their speech as they see fit."),
  disputatio: t("Scholastic disputation: videtur quod → sed contra → respondeo → ad argumenta. For the humanities."),
  scientific: t("Scientific form: claim → basis ([E#] or explicit assumptions) → what would refute it → alternatives."),
};
const POLICY_INFO: Record<string, string> = {
  unanimous: t("Unanimity: the absence of objections is enough (reservations are allowed)."),
  supermajority: t("Supermajority: at least 2/3 without objections; dissent goes into the report."),
  majority: t("Simple majority: more than half without objections; dissent goes into the report."),
};
const POLICY_NAME: Record<string, string> = { unanimous: t("Unanimity"), supermajority: t("2/3 of votes"), majority: t("Simple majority") };
const FORM_NAME: Record<string, string> = { free: t("Free"), disputatio: t("Disputation"), scientific: t("Scientific") };

export function NewSymposium() {
  const { bootstrap, user, toast, refreshBootstrap } = useApp();
  const [chars, setChars] = useState<Character[] | null>(null);
  const [picked, setPicked] = useState<number[]>([]);
  const [folders, setFolders] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [f, setF] = useState({ title: "", topic: "", completion_criterion: "", report_format: "", language: t("English"), max_rounds: 8, form: "free",
    vote_policy: "unanimous", api_profile_id: 0, token_limit: "" as string, start_now: true });
  useEffect(() => { document.title = t("New symposium"); refreshBootstrap(); get<Character[]>(`/api/characters?lang=${getLang()}`).then(setChars).catch(() => setChars([])); }, []);
  const set = (k: string, v: any) => setF((p) => ({ ...p, [k]: v }));
  const profiles = bootstrap.profiles;
  const prof = profiles.find((p) => p.id === f.api_profile_id) || profiles.find((p) => p.id === user.active_profile_id) || profiles[0] || null;
  const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length >= 7 ? p : [...p, id]));
  const valid = f.title.trim() && f.topic.trim() && picked.length >= 1;

  const submit = async () => {
    setBusy(true);
    try {
      const r = await post<{ id: number }>("/api/symposia", {
        ...f, api_profile_id: f.api_profile_id || null, token_limit: f.token_limit ? Number(f.token_limit) : null,
        members: picked.map((id) => ({ character_id: id })), rag_folder_ids: folders,
      });
      go(`s/${r.id}`);
    } catch (e: any) { toast(e.message); setBusy(false); }
  };
  return (
    <div className="page scroll">
      <div className="page-in narrow">
        <h1 style={{ marginBottom: 4 }}>{t("New symposium")}</h1>
        <p className="muted" style={{ margin: "0 0 20px" }}>{t("The topic and the criterion set the goal; the secretary derives a checklist from the criterion, by which the engine knows when to wrap up.")}</p>
        <section className="form-sec">
          <Field label={t("Title")}><input className="input" value={f.title} onChange={(e) => set("title", e.target.value)} placeholder={t("For example: Should we rewrite the system from scratch")} /></Field>
          <Field label={t("Topic")}><textarea className="textarea" value={f.topic} onChange={(e) => set("topic", e.target.value)} placeholder={t("The question or problem the table discusses")} /></Field>
          <Field label={t("Completion criterion")} hint={t("What must be established for the discussion to count as finished.")}><textarea className="textarea" value={f.completion_criterion} onChange={(e) => set("completion_criterion", e.target.value)} /></Field>
          <Field label={t("Final report format")}><input className="input" value={f.report_format} onChange={(e) => set("report_format", e.target.value)} placeholder={t("Conclusions / Disagreements / Open questions")} /></Field>
        </section>

        <section className="form-sec">
          <div className="row"><h3 className="grow">{t("Participants — characters")} <span className="faint">{picked.length}/7</span></h3><a className="btn sm" href="#/characters"><Icon n="users" size={13} />{t("Manage characters")}</a></div>
          <p className="hint" style={{ margin: 0 }}>{t("Pick characters: their persona, manner, model, tools and private folders are pulled in automatically. Any participant can be fine-tuned on the go.")}</p>
          {chars === null ? <div className="empty">{t("Loading…")}</div> : (
            <div className="char-grid">{chars.map((c) => {
              const on = picked.includes(c.id);
              return (
                <button type="button" key={c.id} className={`char-card ${on ? "picked" : ""}`} onClick={() => toggle(c.id)} aria-pressed={on}>
                  <div className="char-top"><CharAvatar c={c} size={44} /><div className="grow" style={{ minWidth: 0 }}><div className="char-name ellipsis">{c.name}</div><div className="faint ellipsis">{c.title}</div></div>{on && <span className="pill accent"><Icon n="check" size={12} />{picked.indexOf(c.id) + 1}</span>}</div>
                  <div className="char-desc">{c.description}</div>
                  <div className="tool-chips"><span className="tag">{tempLabel(c.temperature)}</span>{c.api_profile_id && <span className="tag">{t("own profile")}</span>}</div>
                </button>);
            })}</div>)}
        </section>

        <section className="form-sec">
          <h3>{t("Discussion")}</h3>
          <div className="grid2">
            <Field label={t("Discussion form")} hint={FORM_INFO[f.form]}>
              <select className="select" value={f.form} onChange={(e) => set("form", e.target.value)}>{bootstrap.forms.map((x) => <option key={x} value={x}>{FORM_NAME[x] || x}</option>)}</select>
            </Field>
            <Field label={t("Rule for confirming completion")} hint={POLICY_INFO[f.vote_policy]}>
              <select className="select" value={f.vote_policy} onChange={(e) => set("vote_policy", e.target.value)}>{bootstrap.vote_policies.map((x) => <option key={x} value={x}>{POLICY_NAME[x] || x}</option>)}</select>
            </Field>
            <Field label={t("Round limit")} hint={t("When reached — a final confirmation; the symposium closes whatever the outcome. 0 — no limit.")}>
              <input className="input" type="number" min={0} value={f.max_rounds} onChange={(e) => set("max_rounds", Number(e.target.value))} />
            </Field>
            <Field label={t("Symposium language")}><input className="input" value={f.language} onChange={(e) => set("language", e.target.value)} /></Field>
            <Field label={t("Symposium LLM profile")} hint={ceilingNote(prof)}>
              <select className="select" value={f.api_profile_id} onChange={(e) => set("api_profile_id", Number(e.target.value))}>
                <option value={0}>{t("Default (the owner's active profile)")}</option>{profiles.map((p) => <option key={p.id} value={p.id}>{p.name} — {p.model}</option>)}</select></Field>
            <Field label={t("Token limit for the symposium")} hint={t("Empty — no limit.")}><input className="input" type="number" min={0} value={f.token_limit} onChange={(e) => set("token_limit", e.target.value)} /></Field>
          </div>
        </section>

        <section className="form-sec">
          <h3>{t("Library for the archivist")}</h3>
          <p className="hint" style={{ margin: 0 }}>{t("Folders shared by the whole symposium. You can create a folder and upload files right here. Characters may also have private folders.")}</p>
          <RagPicker selected={folders} onToggle={(id, on) => setFolders((p) => (on ? (p.includes(id) ? p : [...p, id]) : p.filter((x) => x !== id)))} />
        </section>
        <div className="row" style={{ margin: "8px 0 40px" }}>
          <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={f.start_now} onChange={(e) => set("start_now", e.target.checked)} />{t("Start immediately")}</label>
          <span className="spacer" />
          <a className="btn ghost" href="#/">{t("Cancel")}</a>
          <button className="btn primary" disabled={!valid || busy} onClick={submit}>{t("Create symposium")}</button>
        </div>
      </div>
    </div>
  );
}
