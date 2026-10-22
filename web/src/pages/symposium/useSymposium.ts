import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "../../api";
import { Detail, Evidence, Message, Round } from "../../types";
import { t } from "../../i18n";

export type Phase = "waiting" | "thinking" | "writing" | "agent";
export interface Live { memberId: number; text: string; thinking: string; agent?: string; phase: Phase }

/** Скрыть служебные блоки (<state>, <call>) из потока, пока речь ещё печатается. */
export function visibleStream(text: string): string {
  const cut = text.search(/<(state|call)\b/i);
  let t = cut >= 0 ? text.slice(0, cut) : text;
  t = t.replace(/<[a-z]*$/i, "");
  return t.trimEnd();
}

/** Состояние симпозиума: загрузка + подписка на SSE (/events). */
export function useSymposium(id: number) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [rounds, setRounds] = useState<Round[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [voting, setVoting] = useState<{ done: number; total: number } | null>(null);
  const [error, setError] = useState("");
  const timer = useRef<number | undefined>(undefined);

  const loadAll = useCallback(async () => {
    try {
      const [d, t, r] = await Promise.all([get<Detail>(`/api/symposia/${id}`), get<{ rounds: Round[]; messages: Message[] }>(`/api/symposia/${id}/transcript`),
        get<{ evidence: Evidence[] }>(`/api/symposia/${id}/registries`)]);
      setDetail(d); setMessages(t.messages); setRounds(t.rounds); setEvidence(r.evidence); setError("");
    } catch (e: any) { setError(e.message || t("Could not load the symposium")); }
  }, [id]);

  const refresh = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(loadAll, 250);
  }, [loadAll]);

  useEffect(() => {
    loadAll();
    const es = new EventSource(`/api/symposia/${id}/events`);
    es.onmessage = (ev) => {
      let e: any; try { e = JSON.parse(ev.data); } catch { return; }
      switch (e.type) {
        case "hello": loadAll(); break;
        case "message": {
          const m: Message = e.message;
          setMessages((ms) => (ms.some((x) => x.id === m.id) ? ms : [...ms, { ...m, meta: typeof m.meta === "string" ? safeJson(m.meta) : m.meta }]));
          if (m.kind === "member" || m.kind === "skip") setLive((l) => (l && l.memberId === m.member_id ? null : l));
          if (m.kind === "report" || m.kind === "secretary") refresh();
          break;
        }
        case "turn_start": setLive({ memberId: e.member_id, text: "", thinking: "", phase: "waiting" }); break;
        case "turn_end": setLive(null); break;
        case "delta": setLive((l) => (l && l.memberId === e.member_id ? { ...l, text: l.text + e.text, agent: undefined, phase: "writing" } : { memberId: e.member_id, text: e.text, thinking: "", phase: "writing" })); break;
        case "thinking_delta": setLive((l) => (l && l.memberId === e.member_id ? { ...l, thinking: l.thinking + e.text, phase: l.text ? l.phase : "thinking" } : l)); break;
        case "subagent_start": setLive((l) => (l ? { ...l, agent: e.agent, phase: "agent" } : l)); break;
        case "evidence": setEvidence((ev2) => (ev2.some((x) => x.ref === e.evidence.ref) ? ev2 : [...ev2, { ...e.evidence, ok: !!e.evidence.ok }])); refresh(); break;
        case "vote_progress": setVoting({ done: e.done, total: e.total }); break;
        case "vote_result": setVoting(null); refresh(); break;
        case "summary": setRounds((rs) => rs.map((r) => (r.number === e.round ? { ...r, summary: e.summary } : r))); break;
        case "round_start": case "round_end": case "checklist": case "registries": case "state": case "status": case "error":
        case "hitl_request": case "hitl_answered": case "hitl_cancelled": case "rag_folders_changed": case "workspace":
          if (e.type === "error" || e.type === "state" || e.type === "status") setLive(null);
          refresh(); break;
      }
    };
    return () => { es.close(); window.clearTimeout(timer.current); };
  }, [id, loadAll, refresh]);

  return { detail, messages, rounds, evidence, live, voting, error, refresh, reload: loadAll };
}

function safeJson(s: string) { try { return JSON.parse(s); } catch { return null; } }
