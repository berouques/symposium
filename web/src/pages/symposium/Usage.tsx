import { useEffect, useState } from "react";
import { get } from "../../api";
import { Detail } from "../../types";
import { fmtN } from "../../components/ui";
import { t } from "../../i18n";

const TYPE_RU: Record<string, string> = { turn: t("participants' replies"), vote: t("ballots"), secretary: t("secretary"), summary: t("digests (LLM)"), report: t("final report"), subagent: t("subagents"), digest: t("subagents") };

export function Bar({ value, max, hot }: { value: number; max: number; hot?: boolean }) {
  return <div className="bar"><i className={hot ? "hot" : ""} style={{ width: `${max ? Math.max(2, Math.round((value / max) * 100)) : 0}%` }} /></div>;
}

/** Расход токенов одного симпозиума: итог, по участникам, по видам вызовов, по раундам. */
export function UsageTab({ detail }: { detail: Detail }) {
  const [u, setU] = useState<any>(null);
  useEffect(() => { const f = () => get(`/api/symposia/${detail.id}/usage`).then(setU).catch(() => {}); f(); const t = setInterval(f, detail.status === "active" ? 5000 : 30000); return () => clearInterval(t); }, [detail.id, detail.status]);
  if (!u) return <div className="empty">{t("Loading…")}</div>;
  const total = u.tokens_in + u.tokens_out;
  const maxM = Math.max(1, ...u.by_member.map((m: any) => m.tokens_in + m.tokens_out));
  const maxR = Math.max(1, ...u.by_round.map((r: any) => r.tokens_in + r.tokens_out));
  const maxT = Math.max(1, ...u.by_type.map((t: any) => t.tokens_in + t.tokens_out));
  return (
    <div className="pad stack">
      <div className="stat-row">
        <div className="stat"><span>{t("Total tokens")}</span><b>{fmtN(total)}</b>{u.token_limit ? <Bar value={total} max={u.token_limit} hot={total > u.token_limit * 0.85} /> : null}{u.token_limit ? <small>{t("limit")} {fmtN(u.token_limit)}</small> : null}</div>
        <div className="stat"><span>{t("In / out")}</span><b>{fmtN(u.tokens_in)} / {fmtN(u.tokens_out)}</b></div>
        <div className="stat"><span>{t("LLM calls")}</span><b>{u.calls}</b></div>
      </div>
      <p className="hint" style={{ margin: 0 }}>{t("«Out» includes the model's hidden reasoning — it most often explains a large usage.")}</p>
      <h4>{t("By participant")}</h4>
      {u.by_member.map((m: any) => <div className="urow" key={m.member_id}><span>{m.name}</span><Bar value={m.tokens_in + m.tokens_out} max={maxM} /><span className="faint">{fmtN(m.tokens_in)}↑ {fmtN(m.tokens_out)}↓ · {m.calls}</span></div>)}
      <h4>{t("By call type")}</h4>
      {u.by_type.map((t: any) => <div className="urow" key={t.call_type}><span>{TYPE_RU[t.call_type] || t.call_type}</span><Bar value={t.tokens_in + t.tokens_out} max={maxT} /><span className="faint">{fmtN(t.tokens_in + t.tokens_out)} · {t.calls}</span></div>)}
      <h4>{t("By round")}</h4>
      {u.by_round.map((r: any) => <div className="urow" key={r.round}><span>{t("Round")} {r.round}</span><Bar value={r.tokens_in + r.tokens_out} max={maxR} /><span className="faint">{fmtN(r.tokens_in + r.tokens_out)} · {r.calls}</span></div>)}
    </div>
  );
}
