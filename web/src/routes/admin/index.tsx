import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { getAdminStats, runRetention, runCleanup, runStorageAudit } from "@/lib/admin";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <Card>
      <CardContent className="pt-6">
        <p className="text-2xl font-bold">{value}</p>
        <p className="text-sm text-muted-foreground mt-1">{label}</p>
      </CardContent>
    </Card>
  );
}

function MaintenanceButton({
  label,
  onRun,
  result,
}: {
  label: string;
  onRun: () => void;
  result: string | null;
}) {
  return (
    <div className="flex items-center justify-between py-2">
      <div>
        <p className="text-sm font-medium">{label}</p>
        {result && <p className="text-xs text-muted-foreground mt-0.5">{result}</p>}
      </div>
      <Button variant="outline" size="sm" onClick={onRun}>
        Run
      </Button>
    </div>
  );
}

export function AdminDashboard() {
  const { data: stats } = useQuery({ queryKey: ["admin", "stats"], queryFn: getAdminStats });
  const [retentionResult, setRetentionResult] = useState<string | null>(null);
  const [cleanupResult, setCleanupResult] = useState<string | null>(null);
  const [auditResult, setAuditResult] = useState<string | null>(null);

  const retention = useMutation({
    mutationFn: runRetention,
    onSuccess: (d) => setRetentionResult(`Deleted ${d.deleted} front record(s)`),
  });
  const cleanup = useMutation({
    mutationFn: runCleanup,
    onSuccess: (d) => setCleanupResult(`Removed ${d.deleted} file(s), freed ${formatBytes(d.freed_bytes)}`),
  });
  const audit = useMutation({
    mutationFn: runStorageAudit,
    onSuccess: (d) => setAuditResult(`Audited ${d.users_checked} user(s), corrected ${d.users_corrected}`),
  });

  return (
    <>
      <PageHeader title="Admin" />
      <div className="space-y-6 max-w-3xl">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatCard label="Total users" value={stats?.total_users ?? "—"} />
          <StatCard label="Total members" value={stats?.total_members ?? "—"} />
          <StatCard label="Storage used" value={stats ? formatBytes(stats.total_storage_bytes) : "—"} />
          <StatCard
            label="By tier"
            value={
              stats
                ? Object.entries(stats.users_by_tier)
                    .map(([t, n]) => `${t}: ${n}`)
                    .join(" · ")
                : "—"
            }
          />
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Maintenance</CardTitle>
          </CardHeader>
          <CardContent className="divide-y">
            <MaintenanceButton
              label="Run retention"
              onRun={() => retention.mutate()}
              result={retentionResult}
            />
            <MaintenanceButton
              label="Clean up orphaned files"
              onRun={() => cleanup.mutate()}
              result={cleanupResult}
            />
            <MaintenanceButton
              label="Audit storage usage"
              onRun={() => audit.mutate()}
              result={auditResult}
            />
          </CardContent>
        </Card>
      </div>
    </>
  );
}
