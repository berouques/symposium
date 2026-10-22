import { ProfileLite } from "../types";
import { t } from "../i18n";

/** Температура словами: число остаётся видимым, но выбирают характер. */
export const TEMPS: [number, string, string][] = [
  [0.0, t("Determinist"), t("almost no randomness: the same question gets the same answer")],
  [0.1, t("Analyst"), t("strict, precise, minimal liberties")],
  [0.2, t("Pedant"), t("meticulous, point by point")],
  [0.3, t("Methodical"), t("consistent, careful")],
  [0.4, t("Cautious"), t("weighs things, rarely takes risks")],
  [0.5, t("Balanced"), t("the golden mean")],
  [0.6, t("Flexible"), t("ready to leave the template")],
  [0.7, t("Lively"), t("more varied wording and moves")],
  [0.8, t("Inventive"), t("new angles and analogies")],
  [0.9, t("Experimenter"), t("bold hypotheses, unexpected links")],
  [1.0, t("Dreamer"), t("maximum imagination — quirks possible")],
];
export const tempLabel = (t: number) => {
  const near = TEMPS.reduce((a, b) => (Math.abs(b[0] - t) < Math.abs(a[0] - t) ? b : a));
  return `${t.toFixed(1)} · ${near[1]}`;
};

export function TempPicker({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const near = TEMPS.reduce((a, b) => (Math.abs(b[0] - value) < Math.abs(a[0] - value) ? b : a));
  return (
    <div>
      <div className="seg seg11" role="radiogroup" aria-label={t("Temperature")}>
        {TEMPS.map(([v, name]) => (
          <button key={v} type="button" role="radio" aria-checked={near[0] === v} className={`seg-b ${near[0] === v ? "on" : ""}`} onClick={() => onChange(v)}>
            <b>{v.toFixed(1)}</b><span>{name}</span></button>))}
      </div>
      <div className="hint" style={{ marginTop: 4 }}>{near[2]}</div>
    </div>
  );
}

const ORDER: Record<string, number> = { low: 1, medium: 2, high: 3 };
export const DEPTH_NAME: Record<string, string> = { low: t("low"), medium: t("medium"), high: t("high") };

/** Что разрешает модель (профиль): потолок для участников. */
export function ceilingNote(p: ProfileLite | null | undefined): string {
  if (!p) return "";
  if (p.thinking_mode === "off") return t("Model reasoning is switched off in the profile — participants cannot turn it on.");
  if (p.thinking_mode === "on") return t("Model reasoning is on{0}.", p.thinking_depth ? `, уровень не выше «${DEPTH_NAME[p.thinking_depth]}»` : "");
  return t("The profile does not restrict reasoning: the provider decides (models like GLM-5.3 always think — a token reserve is added automatically).");
}

export function ThinkingPicker({ mode, depth, onChange, profile, inheritLabel = t("As the model") }: {
  mode: string | null; depth: string | null; profile?: ProfileLite | null; inheritLabel?: string;
  onChange: (mode: string | null, depth: string | null) => void;
}) {
  const modelOff = profile?.thinking_mode === "off";
  const cap = profile?.thinking_mode === "on" ? profile.thinking_depth : null;
  const m = mode || "";
  return (
    <div className="think-pick">
      <select className="select" value={m} onChange={(e) => onChange(e.target.value || null, e.target.value === "on" ? depth : null)}>
        <option value="">{inheritLabel}</option><option value="off">{t("Off")}</option><option value="on" disabled={modelOff}>{t("On")}{modelOff ? t(" (forbidden by the model)") : ""}</option>
      </select>
      {m === "on" && (
        <select className="select" value={depth || ""} onChange={(e) => onChange("on", e.target.value || null)}>
          <option value="">{t("Effort: model decides")}{cap ? t(" (up to «{0}»)", DEPTH_NAME[cap]) : ""}</option>
          {(["low", "medium", "high"] as const).map((d) => <option key={d} value={d} disabled={!!cap && ORDER[d] > ORDER[cap]}>{t("Effort:")} {DEPTH_NAME[d]}</option>)}
        </select>)}
    </div>
  );
}
