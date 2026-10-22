import { useEffect, useState } from "react";
import { get } from "../../api";
import { Detail } from "../../types";
import { Icon } from "../../components/Icon";
import { fmtN } from "../../components/ui";
import { Live } from "./useSymposium";
import { tempLabel } from "../../components/pickers";
import { t } from "../../i18n";

const FORM_NAME: Record<string, string> = { free: t("free form"), disputatio: t("disputation"), scientific: t("scientific form") };
const POLICY_NAME: Record<string, string> = { unanimous: t("unanimity"), supermajority: t("2/3 of votes"), majority: t("majority") };

function Meter({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = Math.min(100, Math.round((used / limit) * 100));
  return <div className="meter" title={t("{0} of {1}", used, limit)}><span>{label}</span><div className="bar"><i style={{ width: `${pct}%` }} className={pct > 85 ? "hot" : ""} /></div><span className="faint">{fmtN(used)}/{fmtN(limit)}</span></div>;
}

function Limits({ sid, status }: { sid: number; status: string }) {
  const [u, setU] = useState<any>(null);
  useEffect(() => { const f = () => get(`/api/symposia/${sid}/usage`).then((x) => setU(x.profile_limits)).catch(() => {}); f(); const t = setInterval(f, status === "active" ? 6000 : 20000); return () => clearInterval(t); }, [sid, status]);
  if (!u) return null;
  const rows = [[t("per min"), u.rpm], [t("per day"), u.rpd], [t("tok/min"), u.tpm], [t("tok/day"), u.tpd], [t("tok/month"), u.tokens_per_month], [t("per sec"), u.rps], [t("per hour"), u.rph]]
    .filter(([, v]: any) => v && v.limit) as [string, { used: number; limit: number }][];
  return (
    <section className="lsec">
      <h4>{t("Request limits")}</h4>
      {!u.enabled || rows.length === 0 ? <div className="faint">{t("The profile's limiter is off")}</div> : rows.map(([l, v]) => <Meter key={l} label={l} used={v.used} limit={v.limit} />)}
      {u.policy?.max_concurrent ? <div className="faint">{t("in parallel:")} {u.in_flight}/{u.policy.max_concurrent}</div> : null}
      {u.cooldown_s > 0 && <div className="pill warn">{t("provider pause:")} {Math.ceil(u.cooldown_s)} {t("s")}</div>}
    </section>
  );
}

const PHASE: Record<string, string> = { waiting: t("waiting for the model"), thinking: t("thinking…"), writing: t("writing…"), agent: t("searching/computing…") };

export function Left({ detail, live, onMember }: { detail: Detail; live: Live | null; onMember: (id: number) => void }) {
  const names = Object.fromEntries(detail.members.map((m) => [m.id, m.name]));
  const open = detail.issues.filter((i) => i.status === "open");
  const done = detail.checklist.filter((c) => c.status === "done").length;
  return (
    <aside className="left scroll">
      <section className="lsec">
        <h4>{t("Participants")}</h4>
        {detail.members.map((m) => {
          const on = live?.memberId === m.id;
          return (
            <button className={`mem ${on ? "speaking" : ""} ${m.enabled ? "" : "off"}`} key={m.id} onClick={() => onMember(m.id)} title={t("Configure the participant")}>
              <img className="avatar" width={32} height={32} src={`/api/symposia/${detail.id}/members/${m.id}/avatar`} alt="" style={{ boxShadow: `0 0 0 2px ${m.avatar_color}` }} />
              <div className="grow">
                <div className="mem-name">{m.name}{on && <span className="dot live" />}</div>
                <div className={`faint ellipsis ${on ? "phase-txt " + live!.phase : ""}`}>{on ? PHASE[live!.phase] : m.enabled ? `${m.role} · ${tempLabel(m.temperature).split(" · ")[1]}` : t("disabled")}</div>
              </div>
              <span className="faint tok" title={t("Tokens in+out")}>{fmtN(m.tokens.tokens_in + m.tokens.tokens_out)}</span>
            </button>);
        })}
      </section>
      <section className="lsec">
        <h4>{t("Flow")}</h4>
        <div className="kv"><span>{t("Round")}</span><b>{detail.current_round}{detail.max_rounds ? t(" of {0}", detail.max_rounds) : ""}</b></div>
        {detail.max_rounds ? <div className="bar thin"><i style={{ width: `${Math.min(100, (detail.current_round / detail.max_rounds) * 100)}%` }} /></div> : null}
        <div className="kv"><span>{t("Form")}</span><b>{FORM_NAME[detail.form] || detail.form}</b></div>
        <div className="kv"><span>{t("Completion")}</span><b>{POLICY_NAME[detail.vote_policy] || detail.vote_policy}</b></div>
        {detail.recovery_attempts > 0 && <div className="kv"><span>{t("Auto-recoveries")}</span><b>{detail.recovery_attempts}</b></div>}
        <div className="kv"><span>{t("Tokens")}</span><b>{fmtN(detail.usage.tokens_in + detail.usage.tokens_out)}{detail.token_limit ? ` / ${fmtN(detail.token_limit)}` : ""}</b></div>
        {detail.api_profile && <div className="kv"><span>{t("Model")}</span><b className="ellipsis" title={detail.api_profile.model}>{detail.api_profile.model}</b></div>}
      </section>
      <section className="lsec">
        <h4>{t("Criterion checklist")} {detail.checklist.length > 0 && <span className="faint">{done}/{detail.checklist.length}</span>}</h4>
        {detail.checklist.length === 0 ? <div className="faint">{t("Appears once the symposium is opened.")}</div> : detail.checklist.map((c) => (
          <div className={`ck ${c.status}`} key={c.ref}><span className="ck-box">{c.status === "done" && <Icon n="check" size={12} />}</span><span><b>{c.ref}</b> {c.text}</span></div>))}
      </section>
      {detail.issues.length > 0 && (
        <section className="lsec">
          <h4>{t("Objections")} {open.length > 0 && <span className="pill warn">{open.length} {t("open")}</span>}</h4>
          {detail.issues.map((i) => (
            <div className={`issue ${i.status}`} key={i.ref}>
              <div className="row"><b>{i.ref}</b><span className="faint">{names[i.owner_member_id ?? -1] || "?"}</span><span className="spacer" /><span className={`pill ${i.status === "open" ? "warn" : "ok"}`}>{i.status === "open" ? t("open") : t("removed")}</span></div>
              <div>{i.text}</div>{i.satisfied_if && i.status === "open" && <div className="faint">{t("would satisfy:")} {i.satisfied_if}</div>}
            </div>))}
        </section>
      )}
      <Limits sid={detail.id} status={detail.status} />
    </aside>
  );
}
