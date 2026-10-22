import { useEffect, useState } from "react";
import { del, get, patch, post } from "../../api";
import { useApp } from "../../app";
import { LimitPolicy, Preset, Profile } from "../../types";
import { Icon } from "../../components/Icon";
import { Field, Modal, fmtN } from "../../components/ui";
import { t } from "../../i18n";

const EMPTY: LimitPolicy = { enabled: false, max_concurrent: null, min_gap_ms: 0, rps: null, rpm: null, rph: null, rpd: null, tpm: null, tpd: null,
  tokens_per_month: null, on_exhausted: "pause", respect_retry_after: true };
const NUM: [keyof LimitPolicy, string, string][] = [
  ["max_concurrent", t("Simultaneous calls"), t("How many requests to the provider may run at the same time.")],
  ["min_gap_ms", t("Pause between calls, ms"), t("Minimum interval between request starts.")],
  ["rps", t("Requests per second"), ""], ["rpm", t("Requests per minute"), ""], ["rph", t("Requests per hour"), ""],
  ["rpd", t("Requests per day"), t("Counted in UTC; resets at 00:00 UTC.")],
  ["tpm", t("Tokens per minute"), t("In + out.")], ["tpd", t("Tokens per day"), t("In UTC.")], ["tokens_per_month", t("Tokens per month"), t("Calendar month, UTC.")],
];

function LimitsEditor({ value, onChange }: { value: LimitPolicy; onChange: (v: LimitPolicy) => void }) {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [pk, setPk] = useState("");
  useEffect(() => { get<Preset[]>("/api/admin/limit-presets").then(setPresets).catch(() => {}); }, []);
  const set = (k: keyof LimitPolicy, v: any) => onChange({ ...value, [k]: v });
  const cur = presets.find((p) => p.key === pk);
  return (
    <div className="limits">
      <div className="row"><h3 className="grow">{t("Request limiter")}</h3>
        <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={value.enabled} onChange={(e) => set("enabled", e.target.checked)} />{t("Enabled")}</label></div>
      <p className="hint" style={{ margin: "0 0 8px" }}>{t("Everything is off by default: the system imposes nothing. Enable the limits you need, or apply a preset and adjust it to your account.")}</p>
      <Field label={t("Provider preset")}>
        <select className="select" value={pk} onChange={(e) => { setPk(e.target.value); const p = presets.find((x) => x.key === e.target.value); if (p) onChange({ ...EMPTY, ...p.policy }); }}>
          <option value="">{t("— choose a preset —")}</option>{presets.map((p) => <option key={p.key} value={p.key}>{p.title}</option>)}</select>
      </Field>
      {cur && <p className="hint">{cur.note} {cur.source && <a href={cur.source} target="_blank" rel="noreferrer">{t("Source")}</a>}</p>}
      <div className={`grid3 ${value.enabled ? "" : "dim"}`}>
        {NUM.map(([k, label, hint]) => (
          <Field key={k} label={label} hint={hint}>
            <input className="input" type="number" min={0} placeholder={t("unlimited")} value={(value[k] as number | null) ?? ""} disabled={!value.enabled}
              onChange={(e) => set(k, e.target.value === "" ? (k === "min_gap_ms" ? 0 : null) : Number(e.target.value))} />
          </Field>))}
        <Field label={t("When the daily/monthly limit is exhausted")} hint={t("Pause — the symposium stops with a clear message; wait — it waits for the reset.")}>
          <select className="select" value={value.on_exhausted} disabled={!value.enabled} onChange={(e) => set("on_exhausted", e.target.value)}><option value="pause">{t("Pause the symposium")}</option><option value="wait">{t("Wait for the reset")}</option></select></Field>
        <Field label={t("Provider's Retry-After")}><label className="row" style={{ gap: 6 }}><input type="checkbox" checked={value.respect_retry_after} onChange={(e) => set("respect_retry_after", e.target.checked)} />{t("Honour it on an HTTP 429 reply")}</label></Field>
      </div>
    </div>
  );
}

function Editor({ p, onClose, onSaved }: { p: Profile | null; onClose: () => void; onSaved: () => void }) {
  const { toast, refreshBootstrap } = useApp();
  const [f, setF] = useState({ name: p?.name || "", base_url: p?.base_url || "", api_key: "", model: p?.model || "", supports_thinking: p?.supports_thinking || false, extra_body: p?.extra_body || "", thinking_mode: p?.thinking_mode === "off" ? "off" : "on", thinking_depth: "" });
  const [limits, setLimits] = useState<LimitPolicy>(p?.limits || EMPTY);
  const save = async () => {
    try {
      const body: any = { ...f, limits }; if (!body.api_key) delete body.api_key;
      if (p) await patch(`/api/admin/profiles/${p.id}`, body); else await post("/api/admin/profiles", body);
      onSaved(); refreshBootstrap(); onClose();
    } catch (e: any) { toast(e.message); }
  };
  return (
    <Modal wide title={p ? t("Profile: {0}", p.name) : t("New LLM profile")} onClose={onClose} footer={<><button className="btn" onClick={onClose}>{t("Cancel")}</button><button className="btn primary" onClick={save} disabled={!f.name || !f.base_url || !f.model}>{t("Save")}</button></>}>
      <div className="grid2">
        <Field label={t("Title")}><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label={t("Model")}><input className="input" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} placeholder="glm-5.3-flash" /></Field>
      </div>
      <Field label={t("Base URL (OpenAI-compatible)")}><input className="input" value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder="https://openrouter.ai/api/v1" /></Field>
      <Field label={t("API key")} hint={p ? t("Currently: {0}. Leave empty to keep it.", p.api_key_masked || "не задан") : undefined}><input className="input" type="password" autoComplete="off" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} /></Field>
      <div className="grid2">
        <Field label={t("Model reasoning")} hint={t("Master switch: turn it off to save tokens — then reasoning does not work for characters and participants, whatever their settings say. The effort level is set per character.")}>
          <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={f.thinking_mode !== "off"} onChange={(e) => setF({ ...f, thinking_mode: e.target.checked ? "on" : "off", thinking_depth: "" })} />{t("Reasoning enabled")}</label></Field>
        <Field label={t("Extra request parameters (JSON)")} hint={undefined}><input className="input mono" value={f.extra_body} onChange={(e) => setF({ ...f, extra_body: e.target.value })} placeholder='{"provider": {"order": ["..."]}}' /></Field>
      </div>
      <LimitsEditor value={limits} onChange={setLimits} />
    </Modal>
  );
}

function usageLine(u: any): string {
  if (!u || !u.enabled) return t("limiter off");
  const parts: string[] = [];
  for (const [k, l] of [["rpm", t("per min")], ["rpd", t("per day")], ["tpm", t("tok/min")], ["tokens_per_month", t("tok/month")]] as const) if (u[k]?.limit) parts.push(`${fmtN(u[k].used)}/${fmtN(u[k].limit)} ${l}`);
  return parts.join(" · ") || t("enabled, no numeric limits");
}

export function Profiles() {
  const { toast } = useApp();
  const [list, setList] = useState<Profile[]>([]);
  const [edit, setEdit] = useState<Profile | null | "new">(null);
  const [test, setTest] = useState<Record<number, string>>({});
  const load = () => get<Profile[]>("/api/admin/profiles").then(setList).catch((e) => toast(e.message));
  useEffect(() => { load(); }, []);
  const probe = async (p: Profile) => { setTest({ ...test, [p.id]: t("checking…") }); const r = await post(`/api/admin/profiles/${p.id}/test`); setTest({ ...test, [p.id]: `${r.ok ? "✓" : "✗"} ${r.detail}` }); };
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}><p className="muted grow" style={{ margin: 0 }}>{t("Connections to LLM providers. Request limits are configured per profile.")}</p>
        <button className="btn primary" onClick={() => setEdit("new")}><Icon n="plus" size={15} />{t("New profile")}</button></div>
      <div className="list">{list.map((p) => (
        <div className="card prof" key={p.id}>
          <div className="row"><b className="grow">{p.name}</b><span className="pill">{p.model}</span></div>
          <div className="muted mono ellipsis">{p.base_url}</div>
          <div className="faint">{usageLine(p.limits_usage)}</div>
          {test[p.id] && <div className={test[p.id].startsWith("✓") ? "ok-t" : "bad-t"}>{test[p.id]}</div>}
          <div className="row" style={{ marginTop: 6 }}><button className="btn sm" onClick={() => probe(p)}>{t("Test")}</button><button className="btn sm" onClick={() => setEdit(p)}>{t("Edit")}</button>
            <span className="spacer" /><button className="btn sm danger" onClick={async () => { if (confirm(t("Delete profile «{0}»?", p.name))) { await del(`/api/admin/profiles/${p.id}`).catch((e) => toast(e.message)); load(); } }}><Icon n="trash" size={13} /></button></div>
        </div>))}
        {list.length === 0 && <div className="empty card">{t("No profiles. Add a connection to an LLM.")}</div>}</div>
      {edit && <Editor p={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={load} />}
    </>
  );
}
