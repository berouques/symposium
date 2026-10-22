import { useEffect, useState } from "react";
import { get } from "../api";
import { SymposiumSummary } from "../types";
import { Icon } from "../components/Icon";
import { ago, fmtN, statusLabel, statusTone } from "../components/ui";
import { t } from "../i18n";

export function Home() {
  const [list, setList] = useState<SymposiumSummary[] | null>(null);
  useEffect(() => { document.title = "Symposium"; const load = () => get<SymposiumSummary[]>("/api/symposia").then(setList).catch(() => setList([])); load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, []);
  return (
    <div className="page scroll">
      <div className="page-in">
        <div className="row" style={{ marginBottom: 18 }}>
          <h1 className="grow">{t("Symposia")}</h1>
          <a className="btn primary" href="#/new"><Icon n="plus" size={15} />{t("New symposium")}</a>
        </div>
        {list === null ? <div className="empty">{t("Loading…")}</div> : list.length === 0 ? (
          <div className="empty card" style={{ padding: 48 }}>
            <p style={{ fontSize: 17, margin: "0 0 6px" }}>{t("No symposia yet")}</p>
            <p className="muted" style={{ margin: "0 0 16px" }}>{t("Set a topic and a completion criterion, gather participants — and the table starts talking.")}</p>
            <a className="btn primary" href="#/new">{t("Create the first one")}</a>
          </div>
        ) : (
          <div className="list">
            {list.map((s) => (
              <a key={s.id} className="list-item card" href={`#/s/${s.id}`}>
                <div className="grow">
                  <div className="li-title">{s.title}</div>
                  <div className="li-topic ellipsis2 muted">{s.topic}</div>
                </div>
                <div className="li-meta">
                  <span className={`pill ${statusTone[s.status] || ""}`}>{statusLabel[s.status] || s.status}</span>
                  <span className="faint">{t("round")} {s.current_round} · {s.members} {t("part.")}</span>
                  <span className="faint">{ago(s.last_activity)}</span>
                </div>
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
