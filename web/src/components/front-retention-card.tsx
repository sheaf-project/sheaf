import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { useAuth } from "@/hooks/use-auth";
import { getRetention, updateRetention } from "@/lib/retention";
import { getSystemSafety } from "@/lib/system-safety";
import { apiErrorMessage } from "@/lib/api-errors";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import type {
  RetentionSettings,
  RetentionUpdate,
  SafetyChangeRequest,
} from "@/types/api";

// Fixed server-side grace before a freshly imported front can be aged out.
// Mirrored here only for the explanatory copy; the sweep enforces it.
const IMPORT_GRACE_DAYS = 14;

function windowToString(days: number): string {
  return days === 0 ? "" : String(days);
}

// "" (empty) or 0 both mean "off = keep forever".
function parseWindow(input: string): number | "invalid" {
  const trimmed = input.trim();
  if (trimmed === "") return 0;
  const n = Number(trimmed);
  if (!Number.isInteger(n) || n < 0) return "invalid";
  return n;
}

function windowDisplay(days: number): string {
  return days === 0
    ? "Off (fronting history kept forever)"
    : `${days} day${days === 1 ? "" : "s"}`;
}

// 0 = off = the loosest possible window, so it compares as +infinity. A move
// to a strictly smaller effective window (enabling from off, or shortening) is
// the destructive direction. NOTE: this is the opposite keying to the revision
// retention card's isLoosening, and is written from the front-retention rule
// directly rather than reused.
function toEffective(days: number): number {
  return days === 0 ? Infinity : days;
}

function timeRemaining(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "any moment now";
  const hours = Math.ceil(ms / 3_600_000);
  if (hours < 24) return `in ${hours}h`;
  const days = Math.ceil(ms / 86_400_000);
  return `in ${days} day${days === 1 ? "" : "s"}`;
}

export function FrontRetentionCard() {
  const { data } = useQuery({
    queryKey: ["retention"],
    queryFn: getRetention,
  });

  if (!data) return null;
  return <FrontRetentionForm settings={data} />;
}

function FrontRetentionForm({ settings }: { settings: RetentionSettings }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const safety = useQuery({
    queryKey: ["system-safety"],
    queryFn: getSystemSafety,
  });
  const gracePeriod = safety.data?.settings.grace_period_days ?? 0;
  const authTier = safety.data?.settings.auth_tier ?? "none";
  const pendingChange: SafetyChangeRequest | undefined =
    safety.data?.pending_changes.find(
      (c) => "front_retention_days" in c.changes,
    );

  const [draft, setDraft] = useState(
    windowToString(settings.front_retention_days),
  );
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState("");
  const [exportPromptOpen, setExportPromptOpen] = useState(false);

  const parsed = parseWindow(draft);
  const invalid = parsed === "invalid";
  const newDays = parsed === "invalid" ? settings.front_retention_days : parsed;
  const dirty = newDays !== settings.front_retention_days;
  // Destructive direction: enabling (off -> N) or shortening (N -> M, M < N).
  const destructive =
    toEffective(newDays) < toEffective(settings.front_retention_days);
  const needsReauth = destructive && gracePeriod > 0;

  const mutation = useMutation({
    mutationFn: updateRetention,
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["retention"] });
      qc.invalidateQueries({ queryKey: ["system-safety"] });
      setPassword("");
      setTotpCode("");
      setError("");
      setExportPromptOpen(false);
      setDraft(windowToString(res.front_retention_days));
      if (res.front_retention_days === newDays) {
        toast.success("Front retention settings saved");
      } else {
        // Deferred: the change lands as a pending SafetyChangeRequest and
        // finalizes after the grace period.
        toast.success(
          `Change scheduled. It finalizes after the ${gracePeriod}-day grace period, and you can cancel until then.`,
        );
      }
    },
    onError: (err) => setError(apiErrorMessage(err, "Failed")),
  });

  function submit() {
    const patch: RetentionUpdate = { front_retention_days: newDays };
    if (needsReauth) {
      patch.password = password || undefined;
      patch.totp_code = totpCode || undefined;
    }
    mutation.mutate(patch);
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (invalid) {
      setError("Window must be a non-negative whole number of days (0 or empty = off).");
      return;
    }
    if (!dirty) return;
    // The destructive direction gets the "export a copy first" prompt in the
    // path before anything is scheduled for deletion.
    if (destructive) {
      setExportPromptOpen(true);
      return;
    }
    submit();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Fronting History Retention</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Optionally age out your own closed fronting history once it passes a
          window you choose. Off by default: your history is kept forever unless
          you turn this on. Freshly imported history gets a fixed{" "}
          {IMPORT_GRACE_DAYS}-day grace before it can be aged out, so an import
          you want to review does not vanish out from under you. Open fronts are
          never aged out while they are ongoing.
        </p>
        {pendingChange && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm">
            <div className="font-medium">
              Change to{" "}
              {windowDisplay(
                Number(pendingChange.changes.front_retention_days ?? 0),
              )}{" "}
              scheduled.
            </div>
            <div className="text-xs text-muted-foreground">
              Finalizes {timeRemaining(pendingChange.finalize_after)}. You can
              cancel it from{" "}
              <span className="font-medium">System Safety</span> above until
              then.
            </div>
          </div>
        )}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1">
            <Label htmlFor="front-retention-days" className="text-sm">
              Retention window (days)
            </Label>
            <Input
              id="front-retention-days"
              type="number"
              min={0}
              value={draft}
              placeholder="0 = off"
              onChange={(e) => setDraft(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Currently: {windowDisplay(settings.front_retention_days)}. Set 0
              (or leave empty) to turn it off.
            </p>
          </div>
          {needsReauth && (
            <div className="space-y-3 border-t pt-3">
              <p className="text-sm text-muted-foreground">
                Turning retention on or shortening the window schedules deletion
                of history older than the window, so it requires re-auth and
                takes effect after the {gracePeriod}-day grace period. You can
                cancel during that window.
              </p>
              {(authTier === "password" || authTier === "both") && (
                <div className="space-y-1">
                  <Label htmlFor="front-retention-password" className="text-sm">Password</Label>
                  <Input
                    id="front-retention-password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                  />
                </div>
              )}
              {(authTier === "totp" || authTier === "both") &&
                user?.totp_enabled && (
                  <div className="space-y-1">
                    <Label htmlFor="front-retention-totp" className="text-sm">TOTP code</Label>
                    <Input
                      id="front-retention-totp"
                      value={totpCode}
                      onChange={(e) => setTotpCode(e.target.value)}
                      placeholder="6-digit code"
                      inputMode="numeric"
                      maxLength={6}
                      pattern="[0-9]{6}"
                      autoComplete="off"
                      required
                    />
                  </div>
                )}
            </div>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex gap-2">
            <Button
              type="submit"
              size="sm"
              disabled={!dirty || invalid || mutation.isPending}
            >
              {mutation.isPending ? "Saving…" : "Save"}
            </Button>
            {dirty && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() =>
                  setDraft(windowToString(settings.front_retention_days))
                }
                disabled={mutation.isPending}
              >
                Revert
              </Button>
            )}
          </div>
        </form>
        <div className="space-y-2 border-t pt-3 text-xs text-muted-foreground">
          <p>
            Re-importing a backup restores fronting history you have aged out,
            since import cannot tell it was deliberately removed. If you want it
            gone for good, do not re-import an older export of it.
          </p>
          <p>
            Aging out deletes from your live data, which is the privacy promise
            here. Operational backups age out on their own schedule, as
            described in the privacy policy.
          </p>
        </div>
      </CardContent>

      <Dialog open={exportPromptOpen} onOpenChange={setExportPromptOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Export a copy first?</DialogTitle>
            <DialogDescription>
              {newDays === 0
                ? "This will start aging out fronting history."
                : `This will start aging out fronting history older than ${newDays} day${
                    newDays === 1 ? "" : "s"
                  }.`}{" "}
              Once it is aged out, re-importing a backup is the only way back,
              and that defeats the point. Export a copy first if you might want
              one.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setExportPromptOpen(false);
                navigate("/settings/data");
              }}
            >
              Export front history first
            </Button>
            <Button onClick={submit} disabled={mutation.isPending}>
              {mutation.isPending ? "Saving…" : "Continue without exporting"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
