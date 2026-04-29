import { DisplayPreferencesCard } from "@/components/settings/display-preferences-card";
import { ClientSettingsCard } from "@/components/settings/client-settings-card";

export function SettingsAppearancePage() {
  return (
    <>
      <DisplayPreferencesCard />
      <ClientSettingsCard />
    </>
  );
}
