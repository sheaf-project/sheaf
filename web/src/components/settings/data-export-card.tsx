import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { showApiErrorToast } from "@/lib/api-errors";

import { useAuth } from "@/hooks/use-auth";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import {
  createExportJob,
  exportData,
  exportJobDownloadUrl,
  listExportJobs,
  requestAccountData,
  type ExportJob,
  type ExportJobFormat,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type ExportMode = "with_images" | "account_data" | "front_history";
type ExportFormat = "sheaf_native" | "openplural";
type FrontHistoryFormat = "fronts_csv" | "fronts_json" | "fronts_ics";

// Friendly labels for every artefact format a job row can carry.
const FORMAT_LABELS: Record<ExportJobFormat, string> = {
  sheaf_native: "Sheaf native",
  openplural: "OpenPlural",
  fronts_csv: "Front history (CSV)",
  fronts_json: "Front history (JSON)",
  fronts_ics: "Front history (Calendar)",
};

const FRONT_HISTORY_OPTIONS: { value: FrontHistoryFormat; label: string }[] = [
  { value: "fronts_csv", label: "CSV" },
  { value: "fronts_json", label: "JSON" },
  { value: "fronts_ics", label: "Calendar (.ics)" },
];

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
  const { formatDateTime } = useDateFormatters();
  const qc = useQueryClient();
  const [syncing, setSyncing] = useState(false);
  const [mode, setMode] = useState<ExportMode | null>(null);
  const [format, setFormat] = useState<ExportFormat>("sheaf_native");
  const [frontFormat, setFrontFormat] = useState<FrontHistoryFormat>("fronts_csv");
  const [searchParams, setSearchParams] = useSearchParams();
  const highlightJobId = searchParams.get("job");
  // Row refs so we can scroll the highlighted backup into view once
  // the jobs list lands.
  const rowRefs = useRef<Record<string, HTMLDivElement | null>>({});

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

  useEffect(() => {
    if (!highlightJobId || !jobs) return;
    const match = jobs.find((j: ExportJob) => j.id === highlightJobId);
    if (!match) return;
    const node = rowRefs.current[highlightJobId];
    if (node) {
      node.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    // Clear the param so a refresh doesn't keep re-scrolling. We keep
    // it for one render cycle so the highlight ring renders before we
    // strip it.
    const t = setTimeout(() => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.delete("job");
        return next;
      }, { replace: true });
    }, 5000);
    return () => clearTimeout(t);
  }, [highlightJobId, jobs, setSearchParams]);

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
      const data = await exportData(format);
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const date = new Date().toISOString().slice(0, 10);
      const ext = format === "openplural" ? "openplural.json" : "json";
      a.download = `sheaf-export-${date}.${ext}`;
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
        format,
        password,
        totp_code: totp || undefined,
      });
    } else if (mode === "front_history") {
      createJob.mutate({
        // include_images is ignored server-side for the fronts_* formats.
        include_images: false,
        format: frontFormat,
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
            <fieldset className="space-y-2 mb-3">
              <legend className="text-sm font-medium mb-1">Format</legend>
              <p className="text-xs text-muted-foreground mb-2 max-w-prose">
                Applies to both the JSON export here and the full backup below.
              </p>
              <label className="flex items-start gap-2 text-sm cursor-pointer">
                <input
                  type="radio"
                  name="export-format"
                  className="mt-0.5 h-4 w-4 border-input"
                  checked={format === "sheaf_native"}
                  onChange={() => setFormat("sheaf_native")}
                />
                <span>
                  Sheaf (native)
                  <span className="block text-xs text-muted-foreground">
                    Re-importable into another Sheaf instance with full
                    fidelity.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2 text-sm cursor-pointer">
                <input
                  type="radio"
                  name="export-format"
                  className="mt-0.5 h-4 w-4 border-input"
                  checked={format === "openplural"}
                  onChange={() => setFormat("openplural")}
                />
                <span>
                  OpenPlural
                  <span className="block text-xs text-muted-foreground">
                    OpenPlural v0.1, for interchange with other
                    OpenPlural-compatible apps. JSON export here is uri-only;
                    the full backup is a .openplural.zip with image bytes.
                  </span>
                </span>
              </label>
            </fieldset>
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
            <p className="text-xs text-muted-foreground mb-3 max-w-prose">
              Uses the <strong>format</strong> selected above
              {format === "openplural"
                ? " (OpenPlural .openplural.zip)."
                : " (Sheaf native zip)."}
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

          <div className="border-t pt-4">
            <p className="text-sm text-muted-foreground mb-1 max-w-prose">
              <strong>Export front history.</strong> Just your fronting
              history (CSV for spreadsheets, JSON, or a calendar file). Member
              names and notes are included. Builds in the background and shows
              up in the list below, same as a full backup.
            </p>
            <div className="flex items-end gap-2 mt-3">
              <div className="space-y-1">
                <Label htmlFor="front-history-format" className="text-xs">
                  Format
                </Label>
                <Select
                  value={frontFormat}
                  onValueChange={(v) => setFrontFormat(v as FrontHistoryFormat)}
                >
                  <SelectTrigger id="front-history-format" className="w-44">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {FRONT_HISTORY_OPTIONS.map(({ value, label }) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button
                onClick={() => setMode("front_history")}
                variant="outline"
              >
                Export front history
              </Button>
            </div>
          </div>

          {jobs && jobs.length > 0 && (
            <div className="border-t pt-4">
              <p className="text-sm font-medium mb-2">Recent backups</p>
              <div className="space-y-2">
                {jobs.map((j) => (
                  <div
                    key={j.id}
                    ref={(el) => {
                      rowRefs.current[j.id] = el;
                    }}
                    className={`flex items-center justify-between rounded-md border px-3 py-2 text-sm transition-colors ${
                      j.id === highlightJobId
                        ? "ring-2 ring-primary border-primary"
                        : ""
                    }`}
                  >
                    <div className="flex flex-col">
                      <span className="text-xs text-muted-foreground">
                        {FORMAT_LABELS[j.format] ?? j.format}
                        {" · "}
                        {formatDateTime(j.requested_at)}
                      </span>
                      <span className="text-xs">
                        <StatusBadge status={j.status} />{" "}
                        {j.status === "done" && j.expires_at && (
                          <span className="text-muted-foreground">
                            · {formatBytes(j.file_size_bytes)} · expires{" "}
                            {formatDateTime(j.expires_at)}
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
        open={mode === "front_history"}
        onOpenChange={(o) => !o && setMode(null)}
        title="Confirm front-history export"
        description="Re-enter your password to queue an export of your fronting history (member names and notes included). Step-up gated like the other exports."
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
