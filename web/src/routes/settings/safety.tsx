import { SystemSafetyCard } from "@/components/system-safety-card";
import { RevisionRetentionCard } from "@/components/revision-retention-card";

export function SettingsSafetyPage() {
  return (
    <>
      <SystemSafetyCard />
      <RevisionRetentionCard />
    </>
  );
}
