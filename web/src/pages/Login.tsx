import { useState } from "react";
import { post } from "../api";
import { User } from "../types";
import { Logo } from "../components/Logo";
import { Field } from "../components/ui";
import { t } from "../i18n";

export function Login({ onLogin }: { onLogin: (u: User) => void }) {
  const [login, setLogin] = useState(""); const [password, setPassword] = useState(""); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { onLogin((await post<{ user: User }>("/api/auth/login", { login, password })).user); } catch (x: any) { setErr(x.message || t("Could not sign in")); } finally { setBusy(false); }
  };
  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <div className="login-logo"><Logo size={72} live /></div>
        <h1>Symposium</h1>
        <p className="muted">{t("A round table where models talk. You are the moderator.")}</p>
        <Field label={t("Login")}><input className="input" autoFocus autoComplete="username" value={login} onChange={(e) => setLogin(e.target.value)} /></Field>
        <Field label={t("Password")}><input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        {err && <div className="pill danger" role="alert">{err}</div>}
        <button className="btn primary" disabled={busy || !login || !password} style={{ height: 38, justifyContent: "center" }}>{t("Sign in")}</button>
      </form>
    </div>
  );
}
