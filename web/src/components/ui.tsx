import { ReactNode, useEffect } from "react";
import { Icon } from "./Icon";
import { t } from "../i18n";

export function Modal({ title, onClose, children, footer, wide }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} style={wide ? { width: "min(860px,100%)" } : undefined}>
        <header><h2 className="grow">{title}</h2><button className="btn ghost icon" onClick={onClose} aria-label={t("Close")}><Icon n="x" /></button></header>
        <div className="body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </div>
    </div>
  );
}
export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <div className="field"><label>{label}</label>{children}{hint && <span className="hint">{hint}</span>}</div>;
}
export const statusLabel: Record<string, string> = {
  active: t("in progress"), paused_manual: t("paused"), paused_error: t("error"), waiting_human: t("waiting for you"), finished: t("finished"),
};
export const statusTone: Record<string, string> = { active: "accent", paused_manual: "", paused_error: "danger", waiting_human: "warn", finished: "ok" };
export const endReasonLabel: Record<string, string> = {
  vote_passed: t("finished after confirmation"), round_limit_rejected: t("closed at the round limit (with dissent)"), stopped_by_user: t("stopped by you"),
};
export function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return t("just now"); if (s < 3600) return t("{0} min ago", Math.floor(s / 60));
  if (s < 86400) return t("{0} h ago", Math.floor(s / 3600)); return t("{0} d ago", Math.floor(s / 86400));
}
export const fmtN = (n: number) => (n >= 10000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : String(n));
