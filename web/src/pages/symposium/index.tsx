import { useEffect, useRef, useState } from "react";
import { patch, post } from "../../api";
import { useApp } from "../../app";
import { Detail } from "../../types";
import { Icon } from "../../components/Icon";
import { Field, Modal, statusLabel, statusTone } from "../../components/ui";
import { ThinkingPicker, ceilingNote } from "../../components/pickers";
import { Chat, Composer } from "./Chat";
import { Dock } from "./Dock";
import { Left } from "./Left";
import { MemberModal } from "./MemberModal";
import { useSymposium } from "./useSymposium";
import { t } from "../../i18n";

/** Тема, критерий и формат отчёта — всегда под рукой; клик раскрывает/сворачивает. */
function TopicBlock({ detail, onEdit }: { detail: Detail; onEdit: () => void }) {
  const [open, setOpen] = useState(localStorage.getItem("topicOpen") === "1");
  const toggle = () => { setOpen(!open); localStorage.setItem("topicOpen", open ? "0" : "1"); };
  return (
    <div className={`topic-block ${open ? "open" : ""}`}>
      <button className="tb-head" onClick={toggle} aria-expanded={open}>
        <Icon n={open ? "chev" : "chevr"} size={14} />
        <span className="tb-label">{t("Topic")}</span>
        <span className="tb-text ellipsis">{detail.topic_full}</span>
      </button>
      {open && (
        <div className="tb-body">
          <div className="tb-full">{detail.topic_full}</div>
          <dl>
            <dt>{t("Completion criterion")}</dt><dd>{detail.completion_criterion || <span className="faint">{t("not set")}</span>}</dd>
            <dt>{t("Report format")}</dt><dd>{detail.report_format || <span className="faint">{t("free")}</span>}</dd>
          </dl>
          <button className="btn sm" onClick={onEdit}><Icon n="settings" size={13} />{t("Edit topic and criterion")}</button>
        </div>)}
    </div>
  );
}

function SettingsModal({ detail, onClose, onSaved }: { detail: Detail; onClose: () => void; onSaved: () => void }) {
  const { bootstrap, toast } = useApp();
  const [f, setF] = useState({ title: detail.title, topic: detail.topic_full, completion_criterion: detail.completion_criterion, report_format: detail.report_format,
    max_rounds: detail.max_rounds ?? 0, vote_policy: detail.vote_policy, form: detail.form, token_limit: detail.token_limit ?? 0,
    api_profile_id: detail.api_profile?.id ?? 0, fallback_profile_id: detail.fallback_profile_id ?? 0,
    thinking_mode: detail.thinking_mode === "default" ? null : detail.thinking_mode, thinking_depth: detail.thinking_depth });
  const [regen, setRegen] = useState(false);
  const profile = bootstrap.profiles.find((p) => p.id === f.api_profile_id) || null;
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const criterionChanged = f.completion_criterion !== detail.completion_criterion;
  const save = async () => {
    try {
      await patch(`/api/symposia/${detail.id}`, { ...f, thinking_mode: f.thinking_mode || "default", thinking_depth: f.thinking_depth || "" });
      if (regen && criterionChanged) { toast(t("The secretary is regenerating the checklist…")); await post(`/api/symposia/${detail.id}/checklist/regenerate`); }
      onSaved(); onClose();
    } catch (e: any) { toast(e.message); }
  };
  return (
    <Modal wide title={t("Symposium settings")} onClose={onClose} footer={<><button className="btn" onClick={onClose}>{t("Cancel")}</button><button className="btn primary" onClick={save}>{t("Save")}</button></>}>
      <Field label={t("Title")}><input className="input" value={f.title} onChange={(e) => set("title", e.target.value)} /></Field>
      <Field label={t("Topic")} hint={t("Participants learn about the change from the next turn — a note goes into the transcript.")}><textarea className="textarea" value={f.topic} onChange={(e) => set("topic", e.target.value)} /></Field>
      <Field label={t("Completion criterion")}><textarea className="textarea" value={f.completion_criterion} onChange={(e) => set("completion_criterion", e.target.value)} /></Field>
      {criterionChanged && <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={regen} onChange={(e) => setRegen(e.target.checked)} />{t("Regenerate the checklist for the new criterion (calls the secretary)")}</label>}
      <Field label={t("Report format")}><input className="input" value={f.report_format} onChange={(e) => set("report_format", e.target.value)} /></Field>
      <h3 style={{ marginTop: 6 }}>{t("Model")}</h3>
      <div className="grid2">
        <Field label={t("LLM profile")} hint={t("Can be changed during the symposium — from the next call.")}><select className="select" value={f.api_profile_id} onChange={(e) => set("api_profile_id", Number(e.target.value))}>
          <option value={0}>{t("Default (the owner's active profile)")}</option>{bootstrap.profiles.map((p) => <option key={p.id} value={p.id}>{p.name} — {p.model}</option>)}</select></Field>
        <Field label={t("Fallback profile")} hint={t("If the main one is unavailable, auto-recovery switches to this one.")}>
          <select className="select" value={f.fallback_profile_id} onChange={(e) => set("fallback_profile_id", Number(e.target.value))}>
            <option value={0}>{t("None")}</option>{bootstrap.profiles.filter((p) => p.id !== f.api_profile_id).map((p) => <option key={p.id} value={p.id}>{p.name} — {p.model}</option>)}</select></Field>
      </div>
      <Field label={t("Default reasoning for participants")} hint={ceilingNote(profile)}>
        <ThinkingPicker mode={f.thinking_mode} depth={f.thinking_depth} profile={profile} inheritLabel={t("As in the profile")} onChange={(m, d) => setF({ ...f, thinking_mode: m, thinking_depth: d })} /></Field>
      <h3 style={{ marginTop: 6 }}>{t("Discussion")}</h3>
      <div className="grid2">
        <Field label={t("Discussion form")}><select className="select" value={f.form} onChange={(e) => set("form", e.target.value)}>{bootstrap.forms.map((x) => <option key={x}>{x}</option>)}</select></Field>
        <Field label={t("Completion rule")}><select className="select" value={f.vote_policy} onChange={(e) => set("vote_policy", e.target.value)}>{bootstrap.vote_policies.map((x) => <option key={x}>{x}</option>)}</select></Field>
        <Field label={t("Round limit (0 — none)")}><input className="input" type="number" min={0} value={f.max_rounds} onChange={(e) => set("max_rounds", Number(e.target.value))} /></Field>
        <Field label={t("Token limit (0 — none)")}><input className="input" type="number" min={0} value={f.token_limit} onChange={(e) => set("token_limit", Number(e.target.value))} /></Field>
      </div>
    </Modal>
  );
}

function RecoveryBanner({ detail }: { detail: Detail }) {
  const e = detail.error_info!;
  const [left, setLeft] = useState(0);
  useEffect(() => { const f = () => setLeft(e.retry_at ? Math.max(0, Math.round((new Date(e.retry_at).getTime() - Date.now()) / 1000)) : 0); f(); const t = setInterval(f, 1000); return () => clearInterval(t); }, [e.retry_at]);
  return (
    <div className="banner danger" role="alert"><Icon n="alert" />
      <div><b>{t("Stopped because of an error:")} {e.type}</b><div>{e.message}</div>
        {e.recoverable && <div className="muted">{t("Auto-recovery: attempt")} {detail.recovery_attempts + 1}{left > 0 ? t(" in {0} s", left) : t(" — any moment now")}{t(". No need to wait: «Continue».")}</div>}
        {!e.recoverable && <div className="muted">{t("Your decision is needed: change the settings or the profile and press «Continue».")}</div>}</div></div>
  );
}

export function SymposiumPage({ id }: { id: number }) {
  const { setLive, setTitle, toast } = useApp();
  const s = useSymposium(id);
  const { detail } = s;
  const [dockOpen, setDockOpen] = useState(localStorage.getItem("dockOpen") !== "0");
  const [dockW, setDockW] = useState(Number(localStorage.getItem("dockW")) || 540);
  const [settings, setSettings] = useState(false);
  const [memberId, setMemberId] = useState<number | null>(null);
  const drag = useRef(false);

  useEffect(() => { if (detail) { setTitle(detail.title); document.title = `${detail.title} — Symposium`; } return () => setTitle(""); }, [detail?.title]);
  useEffect(() => { setLive(detail?.status === "active"); return () => setLive(false); }, [detail?.status]);
  useEffect(() => {
    const mv = (e: MouseEvent) => { if (drag.current) setDockW(Math.max(320, Math.min(window.innerWidth * 0.7, window.innerWidth - e.clientX))); };
    const up = () => { if (drag.current) { drag.current = false; document.body.style.userSelect = ""; } };
    window.addEventListener("mousemove", mv); window.addEventListener("mouseup", up);
    return () => { window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up); };
  }, []);
  useEffect(() => { localStorage.setItem("dockW", String(Math.round(dockW))); }, [dockW]);

  if (s.error && !detail) return <div className="empty">{s.error}</div>;
  if (!detail) return <div className="empty">{t("Loading…")}</div>;

  const act = (a: string, confirmText?: string) => async () => {
    if (confirmText && !confirm(confirmText)) return;
    try { await post(`/api/symposia/${id}/${a}`); s.refresh(); } catch (e: any) { toast(e.message); }
  };
  const st = detail.status;
  const member = detail.members.find((m) => m.id === memberId);
  return (
    <div className="sym" style={{ ["--dock-w" as any]: `${dockW}px` }}>
      <Left detail={detail} live={s.live} onMember={setMemberId} />
      <section className="center">
        <div className="center-head">
          <div className="grow"><div className="ch-title ellipsis">{detail.title}</div>
            <div className="ch-topic muted ellipsis">{detail.api_profile ? `${detail.api_profile.name} · ${detail.api_profile.model}` : t("no profile selected")}</div></div>
          <span className={`pill ${statusTone[st] || ""}`}>{statusLabel[st] || st}</span>
          {st === "active" && <button className="btn" onClick={act("pause")}><Icon n="pause" size={14} />{t("Pause")}</button>}
          {(st === "paused_manual" || st === "paused_error") && <button className="btn primary" onClick={act("start")}><Icon n="play" size={14} />{st === "paused_error" ? t("Continue") : t("Start")}</button>}
          {st !== "finished" && <button className="btn" onClick={act("stop", t("Finish the symposium now? The secretary will write the final report."))}><Icon n="stop" size={14} />{t("Finish")}</button>}
          <button className="btn ghost icon" onClick={() => setSettings(true)} aria-label={t("Options")} title={t("Symposium settings")}><Icon n="settings" /></button>
          <button className="btn ghost icon" onClick={() => { setDockOpen(!dockOpen); localStorage.setItem("dockOpen", dockOpen ? "0" : "1"); }} aria-label={t("Right panel")} aria-pressed={dockOpen}><Icon n="panel" /></button>
        </div>
        <TopicBlock detail={detail} onEdit={() => setSettings(true)} />
        {st === "paused_error" && detail.error_info && <RecoveryBanner detail={detail} />}
        {st === "waiting_human" && !detail.pending_hitl && <div className="banner warn"><Icon n="hand" />{t("A participant is waiting for your answer.")}</div>}
        <Chat detail={detail} messages={s.messages} rounds={s.rounds} live={s.live} voting={s.voting} evidence={s.evidence} />
        <Composer detail={detail} />
      </section>
      {dockOpen && <>
        <div className="resizer" role="separator" aria-orientation="vertical" onMouseDown={() => { drag.current = true; document.body.style.userSelect = "none"; }} />
        <Dock detail={detail} rounds={s.rounds} messages={s.messages} evidence={s.evidence} onChange={s.reload} />
      </>}
      {settings && <SettingsModal detail={detail} onClose={() => setSettings(false)} onSaved={s.reload} />}
      {member && <MemberModal detail={detail} m={member} onClose={() => setMemberId(null)} onSaved={s.reload} />}
    </div>
  );
}
