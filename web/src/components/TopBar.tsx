import { useEffect, useMemo, useRef, useState } from "react";
import { go, useApp } from "../app";
import { get, patch } from "../api";
import { t } from "../i18n";
import { SymposiumSummary } from "../types";
import { AppSettings } from "./AppSettings";
import { Icon } from "./Icon";
import { Logo } from "./Logo";
import { Field, Modal, ago, statusLabel, statusTone } from "./ui";

function ProfileModal({ onClose }: { onClose: () => void }) {
  const { user, setUser, toast } = useApp();
  const [f, setF] = useState({ name: user.name || "", about: user.about || "" });
  const save = async () => { try { setUser(await patch("/api/auth/me", f)); toast(t("Profile saved")); onClose(); } catch (e: any) { toast(e.message); } };
  return (
    <Modal title={t("My profile")} onClose={onClose} footer={<><button className="btn" onClick={onClose}>{t("Cancel")}</button><button className="btn primary" onClick={save}>{t("Save")}</button></>}>
      <p className="muted" style={{ margin: 0 }}>{t("Participants learn how to address you. Gender, if it matters, can simply be mentioned in the line about you.")}</p>
      <Field label={t("How to address you")}><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder={t("For example: Eugene")} /></Field>
      <Field label={t("About you")} hint={t("One line: profession, interests, how you like answers.")}><input className="input" maxLength={500} value={f.about} onChange={(e) => setF({ ...f, about: e.target.value })} /></Field>
    </Modal>
  );
}

const CHUNK = 40;

/** Slide-in panel with every symposium: search + incremental rendering (works for hundreds). */
function SymposiaDrawer({ onClose }: { onClose: () => void }) {
  const [list, setList] = useState<SymposiumSummary[] | null>(null);
  const [q, setQ] = useState("");
  const [shown, setShown] = useState(CHUNK);
  const sentinel = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => { get<SymposiumSummary[]>("/api/symposia").then(setList).catch(() => setList([])); }, []);
  useEffect(() => { const h = (e: KeyboardEvent) => e.key === "Escape" && onClose(); window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h); }, [onClose]);
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (list || []).filter((s) => !needle || s.title.toLowerCase().includes(needle) || s.topic.toLowerCase().includes(needle));
  }, [list, q]);
  useEffect(() => { setShown(CHUNK); scroller.current?.scrollTo({ top: 0 }); }, [q]);
  useEffect(() => {
    const el = sentinel.current; if (!el || !scroller.current) return;
    const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) setShown((n) => n + CHUNK); }, { root: scroller.current, rootMargin: "200px" });
    io.observe(el); return () => io.disconnect();
  }, [rows.length, shown]);
  return (
    <div className="drawer-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-label={t("Symposia")}>
        <header className="drawer-head">
          <h2 className="grow">{t("Symposia")}</h2>
          <a className="btn primary sm" href="#/new" onClick={onClose}><Icon n="plus" size={13} />{t("New")}</a>
          <button className="btn ghost icon sm" onClick={onClose} aria-label={t("Close")}><Icon n="x" size={14} /></button>
        </header>
        <div className="drawer-search"><input className="input" autoFocus placeholder={t("Search by title or topic")} value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <div className="drawer-list scroll" ref={scroller}>
          {list === null && <div className="empty">{t("Loading…")}</div>}
          {list !== null && rows.length === 0 && <div className="empty">{t("Nothing found")}</div>}
          {rows.slice(0, shown).map((s) => (
            <a key={s.id} className="drawer-item" href={`#/s/${s.id}`} onClick={onClose}>
              <div className="di-title ellipsis">{s.title}</div>
              <div className="di-topic ellipsis2 muted">{s.topic}</div>
              <div className="row" style={{ gap: 8 }}><span className={`pill ${statusTone[s.status] || ""}`}>{t(statusLabel[s.status] || s.status)}</span>
                <span className="faint">{t("round {0}", s.current_round)} · {ago(s.last_activity)}</span></div>
            </a>))}
          {shown < rows.length && <div ref={sentinel} className="empty faint">{t("Loading more…")}</div>}
        </div>
        <footer className="drawer-foot faint">{list ? t("{0} symposia", list.length) : ""}</footer>
      </aside>
    </div>
  );
}

export function TopBar({ route }: { route: string[] }) {
  const { user, theme, toggleTheme, logout, live, title, bootstrap } = useApp();
  const [menu, setMenu] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [profile, setProfile] = useState(false);
  const [settings, setSettings] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const page = route[0] || "";
  useEffect(() => {
    const h = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setMenu(false); };
    document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
  }, []);
  useEffect(() => { setDrawer(false); }, [route.join("/")]);
  const nav = (p: string, label: string, icon: string, active: boolean) => (
    <a className={`nav ${active ? "on" : ""}`} href={`#/${p}`}><Icon n={icon} size={15} /><span>{label}</span></a>
  );
  return (
    <header className="topbar" ref={box}>
      <a className="brand" href="#/" aria-label={t("Symposium — home")}><Logo size={26} live={live} /><span>Symposium</span></a>
      <button className="btn ghost sw-btn" onClick={() => setDrawer(true)} aria-label={t("Open the list of symposia")}>
        <Icon n="layers" size={15} /><span className="sw-title">{page === "s" && title ? title : t("Symposia")}</span><Icon n="chevr" size={13} />
      </button>
      <nav className="topnav">
        {nav("", t("Home"), "scale", page === "" || page === "s" || page === "new")}
        {nav("characters", t("Characters"), "users", page === "characters")}
        {nav("library", t("Library"), "book", page === "library")}
        {nav("usage", t("Usage"), "cpu", page === "usage")}
        {user.is_admin && nav("admin", t("Administration"), "settings", page === "admin")}
      </nav>
      <span className="spacer" />
      <button className="btn ghost icon" onClick={toggleTheme} aria-label={t("Toggle light/dark")} title={t("Toggle light/dark")}><Icon n={theme === "dark" ? "sun" : "moon"} /></button>
      <div className="user">
        <button className="btn ghost" onClick={() => setMenu((v) => !v)}><Icon n="user" /><span className="u-name">{user.name || user.login}</span></button>
        {menu && <div className="dropdown right"><div className="dd-item muted">{user.login}{user.is_admin ? ` · ${t("admin")}` : ""}</div>
          <button className="dd-item" onClick={() => { setMenu(false); setProfile(true); }}><Icon n="edit" size={14} />{t("My profile")}</button>
          <button className="dd-item" onClick={() => { setMenu(false); setSettings(true); }}><Icon n="settings" size={14} />{t("Appearance and language")}</button>
          <a className="dd-item" href="https://github.com/berouques/symposium" target="_blank" rel="noreferrer"><Icon n="code" size={14} />Symposium {bootstrap.version} · GitHub</a>
          <button className="dd-item" onClick={logout}><Icon n="logout" size={14} />{t("Sign out")}</button></div>}
      </div>
      {drawer && <SymposiaDrawer onClose={() => setDrawer(false)} />}
      {profile && <ProfileModal onClose={() => setProfile(false)} />}
      {settings && <AppSettings onClose={() => setSettings(false)} />}
    </header>
  );
}
