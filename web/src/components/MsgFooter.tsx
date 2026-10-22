import { useState } from "react";
import { Message } from "../types";
import { Icon } from "./Icon";
import { copyText } from "./copy";
import { getLang, t } from "../i18n";

export function fmtTime(iso: string): string {
  const d = new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");
  return d.toLocaleString(getLang() === "ru" ? "ru-RU" : "en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Под каждым сообщением: дата и время, токены (текст / мышление), копирование. */
export function MsgFooter({ m, text }: { m: Message; text?: string }) {
  const [done, setDone] = useState(false);
  const think = m.tokens_thinking || 0;
  const body = Math.max(0, (m.tokens_out || 0) - think);
  return (
    <div className="msg-foot">
      <span title={m.created_at}>{fmtTime(m.created_at)}</span>
      {(m.tokens_out > 0 || m.tokens_in > 0) && <span title={t("In (context) → out: text and reasoning")}>
        ⇣{m.tokens_in} {t(" · text")} {body}{think > 0 ? t(" · reasoning {0}", think) : ""}</span>}
      {m.model && <span className="faint ellipsis" style={{ maxWidth: 160 }}>{m.model}</span>}
      <span className="spacer" />
      <button className="cp" onClick={async () => { await copyText(text ?? m.content); setDone(true); setTimeout(() => setDone(false), 1400); }} aria-label={t("Copy message")} title={t("Copy message")}>
        <Icon n={done ? "check" : "copy"} size={13} />{done ? t("Copied") : ""}</button>
    </div>
  );
}
