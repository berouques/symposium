import { useEffect, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { t } from "../../i18n";

/** Интерактивный терминал песочницы: xterm.js ⇄ WebSocket ⇄ PTY на сервере. */
export function Terminal({ sid, active }: { sid: number; active: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const [state, setState] = useState<"connecting" | "open" | "closed">("connecting");
  const [epoch, setEpoch] = useState(0);

  useEffect(() => {
    const css = getComputedStyle(document.documentElement);
    const v = (n: string) => css.getPropertyValue(n).trim();
    const term = new XTerm({ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 13, cursorBlink: true, convertEol: false,
      theme: { background: v("--sunken"), foreground: v("--ink"), cursor: v("--accent"), selectionBackground: v("--accent-soft") } });
    const fit = new FitAddon(); term.loadAddon(fit); term.open(host.current!); fitRef.current = fit;
    try { fit.fit(); } catch { /* контейнер скрыт */ }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/symposia/${sid}/terminal`); ws.binaryType = "arraybuffer";
    const enc = new TextEncoder();
    ws.onopen = () => { setState("open"); ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows })); };
    ws.onmessage = (e) => term.write(typeof e.data === "string" ? e.data : new Uint8Array(e.data));
    ws.onclose = () => { setState("closed"); term.write(t("\r\n\u001b[2m[session closed]\u001b[0m\r\n")); };
    term.onData((d) => ws.readyState === 1 && ws.send(JSON.stringify({ type: "input", data: d })));
    term.onResize(({ cols, rows }) => ws.readyState === 1 && ws.send(JSON.stringify({ type: "resize", cols, rows })));
    const ro = new ResizeObserver(() => { try { fit.fit(); } catch { /* скрыт */ } });
    ro.observe(host.current!);
    void enc;
    return () => { ro.disconnect(); ws.close(); term.dispose(); };
  }, [sid, epoch]);

  useEffect(() => { if (active) setTimeout(() => { try { fitRef.current?.fit(); } catch { /* */ } }, 30); }, [active]);

  return (
    <div className="term-wrap">
      <div className="term-bar"><span className={`dot ${state === "open" ? "live" : ""}`} /><span className="muted">{state === "open" ? t("connected to the sandbox") : state === "connecting" ? t("connecting…") : t("disconnected")}</span>
        <span className="spacer" />{state === "closed" && <button className="btn sm" onClick={() => { setState("connecting"); setEpoch((x) => x + 1); }}>{t("Reconnect")}</button>}</div>
      <div className="term-host" ref={host} />
    </div>
  );
}
