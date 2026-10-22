// Minimal i18n: source strings are English; `t()` returns the translation for the
// current UI language (Russian dictionary in ru.ts), falling back to the English text.
// Placeholders: t("Round {0}", n).
import { ru } from "./ru";

export type Lang = "en" | "ru";
const KEY = "symposium.lang";
let lang: Lang = ((): Lang => {
  const saved = localStorage.getItem(KEY);
  if (saved === "en" || saved === "ru") return saved;
  return "en";                       // English by default after installation
})();

export const getLang = (): Lang => lang;
export function setLang(l: Lang) { lang = l; localStorage.setItem(KEY, l); document.documentElement.lang = l; }

export function t(s: string, ...args: (string | number)[]): string {
  let r = lang === "ru" ? ru[s] ?? s : s;
  args.forEach((a, i) => { r = r.split(`{${i}}`).join(String(a)); });
  return r;
}
document.documentElement.lang = lang;
