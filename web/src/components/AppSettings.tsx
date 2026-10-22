import { useApp } from "../app";
import { getLang, setLang, t } from "../i18n";
import { Field, Modal } from "./ui";

export const PALETTES: { key: string; name: string; note: string; swatch: [string, string, string] }[] = [
  { key: "symposium", name: "Symposium", note: "Verdigris and ink — the default.", swatch: ["#2b7a74", "#f4f5f7", "#0f1219"] },
  { key: "academus", name: "Academus' Garden", note: "Olive groves, marble and shade.", swatch: ["#4f7a3f", "#f2f4ee", "#101510"] },
  { key: "sorbonne", name: "Sorbonne Theological College", note: "Parchment, burgundy and gilding.", swatch: ["#7a1f2b", "#f5efe4", "#1a1213"] },
  { key: "solvay", name: "Solvay Congress", note: "A 1927 blackboard: steel blue and chalk.", swatch: ["#2f4a6b", "#eceeee", "#0e1216"] },
];

/** App-level settings (per browser): palette, light/dark, interface language. */
export function AppSettings({ onClose }: { onClose: () => void }) {
  const { palette, setPalette, mode, setMode } = useApp();
  return (
    <Modal title={t("Appearance and language")} onClose={onClose} footer={<button className="btn primary" onClick={onClose}>{t("Done")}</button>}>
      <Field label={t("Theme")}>
        <div className="palettes">{PALETTES.map((p) => (
          <button key={p.key} type="button" className={`palette ${palette === p.key ? "on" : ""}`} onClick={() => setPalette(p.key)} aria-pressed={palette === p.key}>
            <span className="sw">{p.swatch.map((c) => <i key={c} style={{ background: c }} />)}</span>
            <b>{t(p.name)}</b><small>{t(p.note)}</small></button>))}</div>
      </Field>
      <Field label={t("Brightness")}>
        <div className="seg">{(["light", "dark", "system"] as const).map((m) => (
          <button key={m} type="button" className={`seg-b ${mode === m ? "on" : ""}`} onClick={() => setMode(m)}><span>{m === "light" ? t("Light") : m === "dark" ? t("Dark") : t("System")}</span></button>))}</div>
      </Field>
      <Field label={t("Interface language")} hint={t("Symposium language (what participants speak) is set per symposium.")}>
        <select className="select" value={getLang()} onChange={(e) => { setLang(e.target.value as "en" | "ru"); location.reload(); }}>
          <option value="en">English</option><option value="ru">Русский</option></select>
      </Field>
    </Modal>
  );
}
