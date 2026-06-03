import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { showApiErrorToast } from "@/lib/api-errors";

import { useAuth } from "@/hooks/use-auth";
import {
  createExportJob,
  exportData,
  exportJobDownloadUrl,
  listExportJobs,
  requestAccountData,
  type ExportJob,
} from "@/lib/systems";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type ExportMode = "with_images" | "account_data";

function formatBytes(n: number | null): string {
  if (n === null) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function StatusBadge({ status }: { status: ExportJob["status"] }) {
  const tone = {
    pending: "text-amber-600 dark:text-amber-400",
    running: "text-amber-600 dark:text-amber-400",
    done: "text-emerald-600 dark:text-emerald-400",
    failed: "text-destructive",
    expired: "text-muted-foreground",
  }[status];
  return <span className={`text-xs font-medium ${tone}`}>{status}</span>;
}

function StepUpDialog({
  open,
  onOpenChange,
  title,
  description,
  totpEnabled,
  busy,
  onConfirm,
  confirmLabel,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description: string;
  totpEnabled: boolean;
  busy: boolean;
  onConfirm: (password: string, totp: string) => void;
  confirmLabel: string;
}) {
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");

  function reset(open: boolean) {
    if (!open) {
      setPassword("");
      setTotp("");
    }
    onOpenChange(open);
  }

  const disabled = busy || !password || (totpEnabled && !totp);

  return (
    <Dialog open={open} onOpenChange={reset}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="data-export-password" className="text-sm">Password</Label>
            <Input
              id="data-export-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {totpEnabled && (
            <div className="space-y-1">
              <Label htmlFor="data-export-totp" className="text-sm">TOTP code</Label>
              <Input
                id="data-export-totp"
                value={totp}
                onChange={(e) => setTotp(e.target.value)}
                placeholder="6-digit code"
                inputMode="numeric"
                maxLength={6}
                pattern="[0-9]{6}"
                autoComplete="off"
              />
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => reset(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => onConfirm(password, totp)}
            disabled={disabled}
          >
            {busy ? "Working..." : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function DataExportCard() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [syncing, setSyncing] = useState(false);
  const [mode, setMode] = useState<ExportMode | null>(null);

  const { data: jobs } = useQuery({
    queryKey: ["export-jobs"],
    queryFn: listExportJobs,
    refetchInterval: (query) => {
      const data = query.state.data;
      const inflight =
        Array.isArray(data) &&
        data.some((j: ExportJob) => j.status === "pending" || j.status === "running");
      return inflight ? 5000 : false;
    },
  });

  const createJob = useMutation({
    mutationFn: createExportJob,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["export-jobs"] });
      setMode(null);
      toast.success(
        "Export queued — we'll email you when it's ready (also visible here).",
      );
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Failed to queue export"),
  });

  const fetchAccountData = useMutation({
    mutationFn: requestAccountData,
    onSuccess: (data) => {
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `sheaf-account-data-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setMode(null);
      toast.success("Account data downloaded");
    },
    onError: (err) => showApiErrorToast(err, "Couldn't fetch account data."),
  });

  async function handleSyncJsonExport() {
    setSyncing(true);
    try {
      const data = await exportData();
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `sheaf-export-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success("Data exported");
    } catch (err) {
      // API errors are already toasted by the api-client; this helper
      // skips re-toasting them. Non-API failures (Blob/URL constructor)
      // fall through to the fallback.
      showApiErrorToast(err, "Couldn't export data.");
    } finally {
      setSyncing(false);
    }
  }

  function handleStepUpConfirm(password: string, totp: string) {
    if (mode === "with_images") {
      createJob.mutate({
        include_images: true,
        password,
        totp_code: totp || undefined,
      });
    } else if (mode === "account_data") {
      fetchAccountData.mutate({
        password,
        totp_code: totp || undefined,
      });
    }
  }

  const totpEnabled = !!user?.totp_enabled;
  const busy = createJob.isPending || fetchAccountData.isPending;

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Data export</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <p className="text-sm text-muted-foreground mb-2 max-w-prose">
              Download your plural-system content as JSON: members, fronts,
              groups, tags, custom fields, journals, and revision history.
              Re-importable into another Sheaf instance via{" "}
              <strong>Data import</strong> below.
            </p>
            <Button
              onClick={handleSyncJsonExport}
              variant="outline"
              disabled={syncing}
            >
              {syncing ? "Exporting..." : "Export (JSON only)"}
            </Button>
          </div>

          <div className="border-t pt-4">
            <p className="text-sm text-muted-foreground mb-2 max-w-prose">
              Full backup including image bytes (avatars, journal embeds,
              bio history). Builds in the background — typically a few
              minutes — then we'll email you and show a download button
              here. The file is available for 72 hours.
            </p>
            <p className="text-xs text-muted-foreground mb-3 max-w-prose">
              Note: importing this zip into another Sheaf instance brings
              text content (members, journals, etc.) but image attachments
              need to be re-uploaded by hand. The image bytes are present
              for your records.
            </p>
            <Button onClick={() => setMode("with_images")} variant="outline">
              Build full backup (with images)
            </Button>
          </div>

          <div className="border-t pt-4">
            <p className="text-sm text-muted-foreground mb-2 max-w-prose">
              <strong>Account data (GDPR Article 15).</strong> Everything
              Sheaf knows <em>about</em> your account: identity, sessions,
              IP addresses, API key audit metadata, email delivery state,
              pending safety actions, and notification subscriptions. Not
              re-importable; intended for transparency.
            </p>
            <Button onClick={() => setMode("account_data")} variant="outline">
              Download account data
            </Button>
          </div>

          {jobs && jobs.length > 0 && (
            <div className="border-t pt-4">
              <p className="text-sm font-medium mb-2">Recent backups</p>
              <div className="space-y-2">
                {jobs.map((j) => (
                  <div
                    key={j.id}
                    className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
                  >
                    <div className="flex flex-col">
                      <span className="text-xs text-muted-foreground">
                        {new Date(j.requested_at).toLocaleString()}
                      </span>
                      <span className="text-xs">
                        <StatusBadge status={j.status} />{" "}
                        {j.status === "done" && j.expires_at && (
                          <span className="text-muted-foreground">
                            · {formatBytes(j.file_size_bytes)} · expires{" "}
                            {new Date(j.expires_at).toLocaleString()}
                          </span>
                        )}
                        {j.status === "failed" && j.error && (
                          <span className="text-destructive"> · {j.error}</span>
                        )}
                      </span>
                    </div>
                    {j.status === "done" && (
                      <a
                        href={exportJobDownloadUrl(j.id)}
                        className="text-xs underline"
                      >
                        Download
                      </a>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <StepUpDialog
        open={mode === "with_images"}
        onOpenChange={(o) => !o && setMode(null)}
        title="Confirm full backup"
        description="Re-enter your password to queue a full backup including all your image bytes. This is the highest-value export, so we always require step-up auth."
        totpEnabled={totpEnabled}
        busy={busy}
        onConfirm={handleStepUpConfirm}
        confirmLabel="Queue export"
      />
      <StepUpDialog
        open={mode === "account_data"}
        onOpenChange={(o) => !o && setMode(null)}
        title="Confirm account-data download"
        description="Re-enter your password to download everything Sheaf knows about your account — sessions, IPs, API key audit, etc. Always step-up gated."
        totpEnabled={totpEnabled}
        busy={busy}
        onConfirm={handleStepUpConfirm}
        confirmLabel="Download account data"
      />
    </>
  );
}
