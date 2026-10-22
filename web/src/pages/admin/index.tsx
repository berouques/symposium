import { useApp } from "../../app";
import { Profiles } from "./Profiles";
import { SettingsTab } from "./SettingsTab";
import { Users } from "./Users";
import { Prompts } from "./Prompts";
import { t } from "../../i18n";

const TABS: [string, string][] = [["profiles", t("LLM profiles")], ["settings", t("Settings")], ["prompts", t("Prompts")], ["users", t("Users")]];

export function Admin({ tab }: { tab: string }) {
  const { user } = useApp();
  if (!user.is_admin) return <div className="empty">{t("This section is available to the administrator only.")}</div>;
  return (
    <div className="page scroll">
      <div className="page-in">
        <h1 style={{ marginBottom: 12 }}>{t("Administration")}</h1>
        <div className="subtabs" role="tablist">{TABS.map(([k, l]) => <a key={k} role="tab" aria-selected={tab === k} className={`subtab ${tab === k ? "on" : ""}`} href={`#/admin/${k}`}>{l}</a>)}</div>
        {tab === "profiles" && <Profiles />}{tab === "settings" && <SettingsTab />}{tab === "prompts" && <Prompts />}{tab === "users" && <Users />}
      </div>
    </div>
  );
}
