import { AccountActivityCard } from "@/components/settings/account-activity-card";
import { AccountInfoCard } from "@/components/settings/account-info-card";
import { AdminActivityCard } from "@/components/settings/admin-activity-card";
import { ApiKeysCard } from "@/components/settings/api-keys-card";
import { ActiveSessionsCard } from "@/components/settings/active-sessions-card";
import { PrivacyCard } from "@/components/settings/privacy-card";
import { TrustedDevicesCard } from "@/components/settings/trusted-devices-card";

export function SettingsAccountPage() {
  return (
    <>
      <AccountInfoCard />
      <PrivacyCard />
      <ApiKeysCard />
      <ActiveSessionsCard />
      <TrustedDevicesCard />
      <AdminActivityCard />
      <AccountActivityCard />
    </>
  );
}
