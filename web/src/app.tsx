import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { get, post, setUnauthorizedHandler } from "./api";
import { Bootstrap, User } from "./types";
import { Login } from "./pages/Login";
import { Home } from "./pages/Home";
import { NewSymposium } from "./pages/NewSymposium";
import { SymposiumPage } from "./pages/symposium";
import { Library } from "./pages/Library";
import { Admin } from "./pages/admin";
import { UsagePage } from "./pages/Usage";
import { Characters } from "./pages/Characters";
import { TopBar } from "./components/TopBar";

interface Ctx {
  user: User; bootstrap: Bootstrap; theme: string; toggleTheme: () => void; palette: string; setPalette: (p: string) => void; mode: string; setMode: (m: string) => void; toast: (m: string) => void;
  logout: () => void; setUser: (u: User) => void; refreshBootstrap: () => void; live: boolean; setLive: (v: boolean) => void; title: string; setTitle: (t: string) => void;
}
const AppCtx = createContext<Ctx>(null as unknown as Ctx);
export const useApp = () => useContext(AppCtx);

export function useRoute(): string[] {
  const [h, setH] = useState(location.hash);
  useEffect(() => { const f = () => setH(location.hash); window.addEventListener("hashchange", f); return () => window.removeEventListener("hashchange", f); }, []);
  return h.replace(/^#\/?/, "").split("/").filter(Boolean);
}
export const go = (path: string) => { location.hash = "#/" + path.replace(/^\//, ""); };

function applyTheme(t: string, palette: string) {
  const dark = t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.dataset.palette = palette;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#0F1219" : "#F4F5F7");
}

export function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [theme, setTheme] = useState(localStorage.getItem("theme") || "dark");
  const [palette, setPaletteState] = useState(localStorage.getItem("palette") || "symposium");
  const [msg, setMsg] = useState("");
  const [live, setLive] = useState(false);
  const [title, setTitle] = useState("");
  const route = useRoute();

  useEffect(() => { applyTheme(theme, palette); localStorage.setItem("theme", theme); localStorage.setItem("palette", palette); }, [theme, palette]);
  useEffect(() => { setUnauthorizedHandler(() => setUser(null)); get<User>("/api/auth/me").then(setUser).catch(() => setUser(null)); }, []);
  useEffect(() => { if (user) get<Bootstrap>("/api/bootstrap").then(setBootstrap).catch(() => {}); }, [user]);

  const toast = useCallback((m: string) => { setMsg(m); setTimeout(() => setMsg(""), 3200); }, []);
  const toggleTheme = useCallback(() => {
    setTheme(theme === "dark" ? "light" : "dark");   // тема хранится в браузере
  }, [theme]);
  const logout = useCallback(() => { post("/api/auth/logout").finally(() => setUser(null)); }, []);

  const value = useMemo(() => user && bootstrap ? { user, bootstrap, theme, toggleTheme, palette, setPalette: setPaletteState, mode: theme, setMode: setTheme, toast, logout, setUser: (u: User) => setUser(u), refreshBootstrap: () => { get<Bootstrap>("/api/bootstrap").then(setBootstrap).catch(() => {}); }, live, setLive, title, setTitle } : null,
    [user, bootstrap, theme, palette, toggleTheme, toast, logout, live, title]);

  if (user === undefined) return null;
  if (user === null) return <Login onLogin={(u) => { setUser(u); if (!location.hash) go(""); }} />;
  if (!value) return null;

  const [page, a] = route;
  let body;
  if (page === "new") body = <NewSymposium />;
  else if (page === "s" && a) body = <SymposiumPage key={a} id={Number(a)} />;
  else if (page === "library") body = <Library />;
  else if (page === "usage") body = <UsagePage />;
  else if (page === "characters") body = <Characters />;
  else if (page === "admin") body = <Admin tab={a || "profiles"} />;
  else body = <Home />;
  return (
    <AppCtx.Provider value={value}>
      <div className="app">
        <TopBar route={route} />
        <main className="main">{body}</main>
      </div>
      {msg && <div className="toast" role="status">{msg}</div>}
    </AppCtx.Provider>
  );
}
