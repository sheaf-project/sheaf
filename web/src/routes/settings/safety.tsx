import { SystemSafetyCard } from "@/components/system-safety-card";
import { RevisionRetentionCard } from "@/components/revision-retention-card";
import { FrontRetentionCard } from "@/components/front-retention-card";

export function SettingsSafetyPage() {
  return (
    <>
      <SystemSafetyCard />
      <RevisionRetentionCard />
      <FrontRetentionCard />
    </>
  );
}
