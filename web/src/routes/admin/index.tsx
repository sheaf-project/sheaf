import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  getAdminStats,
  getPushoverUsage,
  runCleanup,
  runRetention,
} from "@/lib/admin";

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
  const { data: pushover } = useQuery({
    queryKey: ["admin", "pushover-usage"],
    queryFn: getPushoverUsage,
  });
  const [retentionResult, setRetentionResult] = useState<string | null>(null);
  const [cleanupResult, setCleanupResult] = useState<string | null>(null);
  const retention = useMutation({
    mutationFn: runRetention,
    onSuccess: (d) => setRetentionResult(`Pruned ${d.pruned} front record(s)`),
  });
  const cleanup = useMutation({
    mutationFn: runCleanup,
    onSuccess: (d) => setCleanupResult(`Removed ${d.total_orphaned} file(s), freed ${formatBytes(d.total_freed_bytes)}`),
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

        {pushover && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Pushover (shared app) usage
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-sm">
                <span className="font-medium">
                  {pushover.count.toLocaleString()}
                </span>{" "}
                {pushover.enforced ? (
                  <>
                    /{" "}
                    <span className="font-medium">
                      {pushover.cap.toLocaleString()}
                    </span>{" "}
                    deliveries this month ({pushover.month}).
                  </>
                ) : (
                  <>deliveries this month ({pushover.month}). No cap enforced.</>
                )}
              </p>
              <p className="text-xs text-muted-foreground">
                Counts deliveries that used the deployment-wide Pushover app
                token. Recipients with a BYO token in their channel config
                aren't tracked here — they hit their own Pushover quota.
              </p>
              {pushover.enforced && pushover.count >= pushover.cap && (
                <p className="text-xs text-destructive">
                  Cap reached. Shared-app deliveries are paused until the
                  next calendar month or until you raise{" "}
                  <code>PUSHOVER_MAX_PER_MONTH</code>.
                </p>
              )}
            </CardContent>
          </Card>
        )}

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
          </CardContent>
        </Card>
      </div>
    </>
  );
}
