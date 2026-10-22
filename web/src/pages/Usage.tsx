import { useEffect, useState } from "react";
import { get } from "../api";
import { fmtN, statusLabel, statusTone } from "../components/ui";
import { Bar } from "./symposium/Usage";
import { t } from "../i18n";

const TYPE_RU: Record<string, string> = { turn: t("participants' replies"), vote: t("ballots"), secretary: t("secretary"), summary: t("digests (LLM)"), report: t("final reports"), subagent: t("subagents") };

/** Расход токенов по всей системе (для администратора) или по своим симпозиумам. */
export function UsagePage() {
  const [g, setG] = useState<any>(null);
  useEffect(() => { document.title = t("Token usage"); get("/api/stats/tokens").then(setG).catch(() => {}); }, []);
  if (!g) return <div className="empty">{t("Loading…")}</div>;
  const days = [...g.by_day].reverse();
  const maxD = Math.max(1, ...days.map((d: any) => d.tokens));
  const maxS = Math.max(1, ...g.by_symposium.map((s: any) => s.tokens));
  const maxP = Math.max(1, ...g.by_profile.map((p: any) => p.tokens));
  return (
    <div className="page scroll"><div className="page-in">
      <h1 style={{ marginBottom: 4 }}>{t("Token usage")}</h1>
      <p className="muted" style={{ margin: "0 0 18px" }}>{g.scope === "all" ? t("Whole system") : t("Your symposia")}{t(". «Out» includes the models' hidden reasoning.")}</p>
      <div className="stat-row">
        <div className="stat"><span>{t("Total tokens")}</span><b>{fmtN(g.total.tokens)}</b></div>
        <div className="stat"><span>{t("In / out")}</span><b>{fmtN(g.total.tokens_in)} / {fmtN(g.total.tokens_out)}</b></div>
        <div className="stat"><span>{t("LLM calls")}</span><b>{g.total.calls}</b></div>
      </div>
      <h3 style={{ margin: "22px 0 8px" }}>{t("By day (last 30)")}</h3>
      <div className="days card">{days.map((d: any) => <div className="day" key={d.day} title={t("{0}: {1} tokens, {2} calls", d.day, d.tokens, d.calls)}><i style={{ height: `${Math.max(3, (d.tokens / maxD) * 100)}%` }} /><span>{d.day.slice(5)}</span></div>)}</div>
      <div className="grid2" style={{ marginTop: 22, alignItems: "start" }}>
        <section><h3 style={{ marginBottom: 8 }}>{t("By call type")}</h3><div className="card pad stack">{g.by_type.map((t: any) => <div className="urow" key={t.call_type}><span>{TYPE_RU[t.call_type] || t.call_type}</span><Bar value={t.tokens} max={g.total.tokens} /><span className="faint">{fmtN(t.tokens)} · {t.calls}</span></div>)}</div></section>
        <section><h3 style={{ marginBottom: 8 }}>{t("By model")}</h3><div className="card pad stack">{g.by_profile.map((p: any) => <div className="urow" key={p.id}><span className="ellipsis" title={p.model}>{p.name}</span><Bar value={p.tokens} max={maxP} /><span className="faint">{fmtN(p.tokens)} · {p.calls}</span></div>)}</div></section>
      </div>
      <h3 style={{ margin: "22px 0 8px" }}>{t("By symposium")}</h3>
      <div className="card">{g.by_symposium.filter((s: any) => s.calls > 0).map((s: any) => (
        <a className="file-row usage-sym" key={s.id} href={`#/s/${s.id}`}><span className="grow ellipsis">{s.title}</span><span className={`pill ${statusTone[s.status] || ""}`}>{statusLabel[s.status] || s.status}</span>
          <span className="usage-bar"><Bar value={s.tokens} max={maxS} /></span><span className="faint w90">{fmtN(s.tokens)}</span><span className="faint w60">{s.calls} {t("calls")}</span></a>))}</div>
    </div></div>
  );
}
