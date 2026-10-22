import { useEffect, useState } from "react";
import { del, get, patch, post } from "../../api";
import { useApp } from "../../app";
import { Icon } from "../../components/Icon";
import { Field, Modal } from "../../components/ui";
import { t } from "../../i18n";

interface U { id: number; login: string; name: string; is_admin: boolean; created_at: string }
export function Users() {
  const { toast, user } = useApp();
  const [list, setList] = useState<U[]>([]); const [add, setAdd] = useState(false);
  const [f, setF] = useState({ login: "", password: "", name: "", is_admin: false });
  const load = () => get<U[]>("/api/admin/users").then(setList).catch((e) => toast(e.message));
  useEffect(() => { load(); }, []);
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}><span className="grow" /><button className="btn primary" onClick={() => setAdd(true)}><Icon n="plus" size={15} />{t("Add user")}</button></div>
      <div className="card">{list.map((u) => (
        <div className="file-row" key={u.id}><Icon n="user" size={15} /><b>{u.login}</b><span className="muted grow">{u.name}</span>
          <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={u.is_admin} disabled={u.id === user.id} onChange={async (e) => { await patch(`/api/admin/users/${u.id}`, { is_admin: e.target.checked }).catch((x) => toast(x.message)); load(); }} />{t("admin")}</label>
          <button className="btn sm danger" disabled={u.id === user.id} onClick={async () => { if (confirm(t("Delete {0}?", u.login))) { await del(`/api/admin/users/${u.id}`).catch((x) => toast(x.message)); load(); } }}><Icon n="trash" size={13} /></button></div>))}</div>
      {add && <Modal title={t("New user")} onClose={() => setAdd(false)} footer={<><button className="btn" onClick={() => setAdd(false)}>{t("Cancel")}</button>
        <button className="btn primary" disabled={!f.login || f.password.length < 4} onClick={async () => { try { await post("/api/admin/users", f); setAdd(false); setF({ login: "", password: "", name: "", is_admin: false }); load(); } catch (e: any) { toast(e.message); } }}>{t("Create")}</button></>}>
        <Field label={t("Login")}><input className="input" value={f.login} onChange={(e) => setF({ ...f, login: e.target.value })} /></Field>
        <Field label={t("Name")}><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label={t("Password")} hint={t("At least 4 characters.")}><input className="input" type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
        <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={f.is_admin} onChange={(e) => setF({ ...f, is_admin: e.target.checked })} />{t("Administrator")}</label></Modal>}
    </>
  );
}
