import { useEffect, useMemo, useRef, useState } from "react";
import { post } from "../../api";
import { useApp } from "../../app";
import { Detail, Evidence, Hitl, Member, Message, Round, StateBlock } from "../../types";
import { Icon } from "../../components/Icon";
import { Markdown } from "../../components/Markdown";
import { MsgFooter } from "../../components/MsgFooter";
import { endReasonLabel } from "../../components/ui";
import { Live, visibleStream } from "./useSymposium";
import { t } from "../../i18n";

export function PhaseBadge({ live }: { live: Live }) {
  const label = live.phase === "agent" ? t("calling a subagent: {0}…", AGENT_NAME[live.agent || ""] || live.agent || "") : live.phase === "thinking" ? t("thinking…") : live.phase === "writing" ? t("writing…") : t("waiting for the model…");
  return <span className={`phase ${live.phase}`}><i className="dot live" />{label}</span>;
}

const AGENT_ICON: Record<string, string> = { web_explorer: "globe", archivarius: "book", coder: "code", ask_human: "hand" };
const AGENT_NAME: Record<string, string> = { web_explorer: t("Web search"), archivarius: t("Archivist"), coder: t("Coder"), ask_human: t("Question to the human") };
const STANCE: Record<string, string> = { agree: t("agrees"), disagree: t("objects"), refine: t("refines") };
const POS: Record<string, string> = { agree: t("for"), reservations: t("for, with reservations"), object: t("objects") };

function Avatar({ sid, m, size = 34 }: { sid: number; m?: Member; size?: number }) {
  return m ? <img className="avatar" width={size} height={size} src={`/api/symposia/${sid}/members/${m.id}/avatar`} alt="" style={{ boxShadow: `0 0 0 2px ${m.avatar_color}` }} />
    : <span className="avatar ph" style={{ width: size, height: size }}><Icon n="user" /></span>;
}

function humanizeSystem(c: string): { text: string; tone: string } | null {
  let m: RegExpMatchArray | null;
  if ((m = c.match(/^\[CONFIRMATION REQUESTED: (.*)\]$/))) return { text: t("Confirmation of completion requested — {0}", m[1]), tone: "" };
  if ((m = c.match(/^\[VOTE REJECTED: (.*)\]$/))) return { text: t("Confirmation rejected. {0}", m[1].replace("Open objections:", "Открытые возражения:")), tone: "warn" };
  if ((m = c.match(/^\[SYMPOSIUM ENDED: (.*)\]$/))) return { text: t("Symposium finished — {0}", m[1]), tone: "ok" };
  if (c === "[SYMPOSIUM STOPPED BY USER]") return { text: t("Symposium stopped by you"), tone: "ok" };
  if ((m = c.match(/^\[RAG_FOLDER_ATTACHED: (.*)\]$/))) return { text: t("Folder «{0}» attached", m[1]), tone: "" };
  if ((m = c.match(/^\[RAG_FOLDER_DETACHED: (.*)\]$/))) return { text: t("Folder «{0}» detached", m[1]), tone: "" };
  if ((m = c.match(/^\[AUTO-RECOVERY (\d+)\/(\d+) after ([\w_]+)\](.*)$/))) return { text: t("Auto-recovery {0}/{1} after error «{2}»{3}", m[1], m[2], m[3], m[4].includes("fallback") ? " — переключено на запасной профиль" : ""), tone: "warn" };
  if ((m = c.match(/^\[CHANGED BY THE HUMAN: (.*)\]$/))) return { text: t("You changed the terms: {0}", m[1].replace("topic →", "тема →").replace("completion criterion →", "критерий →")), tone: "" };
  if (c.startsWith("[CHECKLIST REGENERATED")) return { text: t("Checklist regenerated for the new criterion"), tone: "" };
  if (c.startsWith("[ERROR:")) return null;   // ошибка показана плашкой сверху
  return { text: c, tone: "" };
}

function StateChips({ st, names }: { st: StateBlock; names: Record<string, string> }) {
  const [open, setOpen] = useState(false);
  const stance = Object.entries(st.stance || {});
  const has = (st.claims?.length || 0) + (st.questions?.length || 0) + Object.keys(st.checklist || {}).length > 0;
  return (
    <div className="chips-row">
      {(st.reply_to?.length || 0) > 0 && <span className="chip-s">{t("replies to:")} {st.reply_to.join(", ")}</span>}
      {stance.map(([n, s]) => <span key={n} className={`chip-s ${s}`}>{n}: {STANCE[s] || s}</span>)}
      {st.evidence?.map((e) => <span key={e} className="chip-s ev">{e}</span>)}
      {st.readiness === "settled" && <span className="chip-s ok">{t("position complete")}</span>}
      {st.action === "propose_end" && <span className="chip-s ok">{t("proposes to conclude")}</span>}
      {has && <button className="link" onClick={() => setOpen(!open)}>{open ? t("hide state") : t("state")}</button>}
      {open && (
        <div className="state-box">
          {st.claims?.length > 0 && <div><b>{t("Claims")}</b><ul>{st.claims.map((c, i) => <li key={i}>{c}</li>)}</ul></div>}
          {st.questions?.length > 0 && <div><b>{t("Questions")}</b><ul>{st.questions.map((c, i) => <li key={i}>{c}</li>)}</ul></div>}
          {Object.keys(st.checklist || {}).length > 0 && <div><b>{t("Checklist")}</b> {Object.entries(st.checklist).map(([k, v]) => `${k}: ${v === "done" ? t("done") : t("open")}`).join(" · ")}</div>}
        </div>
      )}
    </div>
  );
}

function MemberMessage({ m, member, sid, names, showThinking }: { m: Message; member?: Member; sid: number; names: Record<string, string>; showThinking: boolean }) {
  const st: StateBlock | undefined = m.meta?.state;
  const flags = m.meta?.flags || {};
  return (
    <article className="msg">
      <Avatar sid={sid} m={member} />
      <div className="msg-body">
        <header className="msg-head"><b style={{ color: member?.avatar_color }}>{member?.name || "?"}</b><span className="faint">{member?.role}</span>
          {flags.state_missing && <span className="chip-s warn" title={t("The participant did not send the state block")}>{t("no state")}</span>}
          {flags.repetition && <span className="chip-s warn" title={t("The reply resembles the previous one")}>{t("repeat")}</span>}
          {flags.truncated && <span className="chip-s warn" title={t("The reply was cut off by the token limit")}>{t("cut off")}</span>}
          <span className="spacer" /><span className="faint tok">{m.tokens_out} {t("tok.")}</span></header>
        {showThinking && m.thinking && <details className="think"><summary>{t("Thoughts")}</summary><pre>{m.thinking}</pre></details>}
        <div className="speech"><Markdown text={m.content} /></div>
        {st && <StateChips st={st} names={names} />}
        <MsgFooter m={m} />
      </div>
    </article>
  );
}

function VoteGroup({ votes, members }: { votes: Message[]; members: Record<number, Member> }) {
  return (
    <div className="vote-card card">
      <div className="vc-title"><Icon n="scale" size={15} />{t("Confirmation of completion")}</div>
      {votes.map((v) => {
        const pos = v.meta?.position as string;
        return (
          <div className="vc-row" key={v.id}>
            <b style={{ color: members[v.member_id!]?.avatar_color }}>{members[v.member_id!]?.name}</b>
            <span className={`pill ${pos === "agree" ? "ok" : pos === "object" ? "danger" : "warn"}`}>{POS[pos] || pos}</span>
            <span className="muted grow">{v.meta?.unresolved && pos !== "agree" ? v.meta.unresolved : v.content}</span>
            {v.meta?.satisfied_if && <span className="faint" title={t("What would remove the objection")}>{t("would remove:")} {v.meta.satisfied_if}</span>}
          </div>
        );
      })}
    </div>
  );
}

function EvidenceCard({ m, member }: { m: Message; member?: Member }) {
  const [open, setOpen] = useState(false);
  const meta = m.meta || {};
  return (
    <div className={`ev-card ${meta.ok === false ? "bad" : ""}`}>
      <div className="row"><Icon n={AGENT_ICON[meta.agent] || "layers"} size={15} /><b>{meta.ref}</b><span className="muted">{AGENT_NAME[meta.agent] || meta.agent}</span>
        <span className="faint ellipsis grow">{t("requested by")} {member?.name}: {meta.query}</span>
        <button className="link" onClick={() => setOpen(!open)}>{open ? t("collapse") : t("expand")}</button></div>
      <div className={`ev-text ${open ? "" : "clamp"}`}><Markdown text={m.content} /></div>
      <MsgFooter m={m} />
    </div>
  );
}

export function Chat({ detail, messages, rounds, live, voting, evidence }: { detail: Detail; messages: Message[]; rounds: Round[]; live: Live | null; voting: { done: number; total: number } | null; evidence: Evidence[] }) {
  const { user } = useApp();
  const members = useMemo(() => Object.fromEntries(detail.members.map((m) => [m.id, m])), [detail.members]);
  const names = useMemo(() => Object.fromEntries(detail.members.map((m) => [m.name, m.name])), [detail.members]);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const roundOf = useMemo(() => Object.fromEntries(rounds.map((r) => [r.id, r])), [rounds]);

  useEffect(() => {
    const el = scroller.current; if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages.length, live?.text, voting]);

  const items: React.ReactNode[] = [];
  let lastRound: number | null | undefined = undefined;
  let votes: Message[] = [];
  const flushVotes = () => { if (votes.length) { items.push(<VoteGroup key={`v${votes[0].id}`} votes={votes} members={members} />); votes = []; } };
  for (const m of messages) {
    if (m.kind !== "vote") flushVotes();
    if (m.round_id !== lastRound && m.round_id != null && m.kind !== "secretary") {
      const r = roundOf[m.round_id];
      if (r) items.push(<RoundMark key={`r${r.id}`} round={r} />);
      lastRound = m.round_id;
    }
    if (m.kind === "member") items.push(<MemberMessage key={m.id} m={m} member={members[m.member_id!]} sid={detail.id} names={names} showThinking={user.show_thinking} />);
    else if (m.kind === "skip") items.push(<div key={m.id} className="sys">{members[m.member_id!]?.name} {t("skips the turn")}</div>);
    else if (m.kind === "human") items.push(<div key={m.id} className="human"><span className="faint">{t("You")}</span><Markdown text={m.content} /><MsgFooter m={m} /></div>);
    else if (m.kind === "evidence") items.push(<EvidenceCard key={m.id} m={m} member={members[m.member_id!]} />);
    else if (m.kind === "vote") votes.push(m);
    else if (m.kind === "secretary") items.push(<div key={m.id} className="opening card"><div className="vc-title"><Icon n="quote" size={15} />{t("Opening of the symposium")}</div><Markdown text={m.content} /><MsgFooter m={m} /></div>);
    else if (m.kind === "report") items.push(<div key={m.id} className="report card"><div className="vc-title"><Icon n="file" size={15} />{t("Final report")}</div><Markdown text={m.content} /><MsgFooter m={m} /></div>);
    else if (m.kind === "system") { const h = humanizeSystem(m.content); if (h) items.push(<div key={m.id} className={`sys ${h.tone}`}>{h.text}</div>); }
  }
  flushVotes();

  const liveMember = live ? members[live.memberId] : undefined;
  const shown = live ? visibleStream(live.text) : "";
  return (
    <div className="chat scroll" ref={scroller} onScroll={(e) => { const t = e.currentTarget; stick.current = t.scrollHeight - t.scrollTop - t.clientHeight < 80; }}>
      <div className="chat-in">
        {messages.length === 0 && !live && <div className="empty">{detail.status === "paused_manual" ? t("The symposium is created and waiting to start.") : t("The secretary is preparing the opening…")}</div>}
        {items}
        {live && (
          <article className="msg live">
            <Avatar sid={detail.id} m={liveMember} />
            <div className="msg-body">
              <header className="msg-head"><b style={{ color: liveMember?.avatar_color }}>{liveMember?.name}</b>
                <PhaseBadge live={live} /></header>
              {shown ? <div className="speech"><Markdown text={shown} /><span className="caret" /></div> : <div className="typing"><i /><i /><i /></div>}
              {!shown && live.thinking && <details className="think" open><summary>{t("Reasoning (")}{live.thinking.length} {t("chars)")}</summary><pre>{live.thinking.slice(-600)}</pre></details>}
            </div>
          </article>
        )}
        {voting && <div className="sys">{t("Confirmation of completion in progress:")} {voting.done} {t("of")} {voting.total}…</div>}
        {detail.status === "finished" && <div className="sys ok">{detail.end_reason ? endReasonLabel[detail.end_reason] || detail.end_reason : t("Symposium finished")}</div>}
        {detail.pending_hitl && <HitlCard sid={detail.id} h={detail.pending_hitl} member={members[detail.pending_hitl.member_id]} />}
      </div>
    </div>
  );
}

function RoundMark({ round }: { round: Round }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="round-mark">
      <span className="line" /><button className="rm-btn" onClick={() => round.summary && setOpen(!open)} disabled={!round.summary}>{t("Round")} {round.number}{round.summary ? (open ? " ▴" : " ▾") : ""}</button><span className="line" />
      {open && round.summary && <pre className="digest">{round.summary}</pre>}
    </div>
  );
}

function HitlCard({ sid, h, member }: { sid: number; h: Hitl; member?: Member }) {
  const { toast } = useApp();
  const [text, setText] = useState(""); const [busy, setBusy] = useState(false);
  const answer = async (option: string) => { setBusy(true); try { await post(`/api/symposia/${sid}/hitl/${h.id}/answer`, { option, text }); } catch (e: any) { toast(e.message); } finally { setBusy(false); } };
  return (
    <div className="hitl card" role="alertdialog" aria-label={t("Participant's question")}>
      <div className="vc-title"><Icon n="hand" size={15} />{member?.name || t("Participant")} {t("asks you")}</div>
      <p className="hitl-q">{h.question}</p>
      {h.context && <p className="muted">{h.context}</p>}
      <div className="row wrap">{h.options?.map((o) => <button key={o} className="btn" disabled={busy} onClick={() => answer(o)}>{o}</button>)}</div>
      <div className="row" style={{ marginTop: 8 }}>
        <input className="input" placeholder={t("Or answer in your own words")} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && text.trim() && answer("")} />
        <button className="btn primary" disabled={busy || !text.trim()} onClick={() => answer("")}>{t("Answer")}</button>
        <button className="btn ghost" disabled={busy} onClick={() => post(`/api/symposia/${sid}/hitl/${h.id}/cancel`).catch((e) => toast(e.message))}>{t("Skip")}</button>
      </div>
    </div>
  );
}

interface Att { name: string; path: string; size: number }

export function Composer({ detail }: { detail: Detail }) {
  const { toast } = useApp();
  const [text, setText] = useState(""); const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<Att[]>([]); const [up, setUp] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const finished = detail.status === "finished";
  const send = async () => {
    if (!text.trim() && files.length === 0) return; setBusy(true);
    try { await post(`/api/symposia/${detail.id}/message`, { text, attachments: files }); setText(""); setFiles([]); } catch (e: any) { toast(e.message); } finally { setBusy(false); }
  };
  const attach = async (list: FileList | null) => {
    if (!list || !list.length) return; setUp(true);
    try {
      const form = new FormData(); Array.from(list).forEach((f) => form.append("files", f));
      const r = await fetch(`/api/symposia/${detail.id}/uploads`, { method: "POST", body: form, credentials: "same-origin" });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      const j = await r.json(); setFiles((p) => [...p, ...(j.files || [])]);
    } catch (e: any) { toast(e.message); } finally { setUp(false); }
  };
  return (
    <div className="composer">
      <div className="composer-in card">
        {files.length > 0 && <div className="composer-files" style={{ flexBasis: "100%" }}>{files.map((f) => (
          <span className="file-chip" key={f.path}><Icon n="file" size={13} /><span className="ellipsis" title={f.path}>{f.name || f.path}</span>
            <button aria-label={t("Remove attachment")} onClick={() => setFiles(files.filter((x) => x.path !== f.path))}><Icon n="x" size={12} /></button></span>))}</div>}
        <button className="btn ghost icon" onClick={() => input.current?.click()} disabled={up} aria-label={t("Attach a file")} title={t("Attach a file — participants can read and analyze it with the coder")}><Icon n="paperclip" /></button>
        <input ref={input} type="file" multiple hidden onChange={(e) => { attach(e.target.files); e.target.value = ""; }} />
        <textarea className="comp-input" rows={1} value={text}
          placeholder={finished ? t("The symposium is finished. Write — the participants will continue the discussion with your message") : t("Say something to the participants — they will take it into account on the next turn")}
          onChange={(e) => { setText(e.target.value); e.target.style.height = "auto"; e.target.style.height = Math.min(160, e.target.scrollHeight) + "px"; }}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
        <button className="btn primary icon" onClick={send} disabled={busy || up || (!text.trim() && files.length === 0)} aria-label={finished ? t("Continue the symposium") : t("Send")} title={finished ? t("Send and continue the symposium") : t("Send")}><Icon n="send" /></button>
      </div>
    </div>
  );
}
