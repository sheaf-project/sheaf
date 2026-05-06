import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useAuth } from "@/hooks/use-auth";
import { getRetention, updateRetention } from "@/lib/retention";
import { getSystemSafety } from "@/lib/system-safety";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { RetentionSettings, RetentionUpdate } from "@/types/api";

interface DraftState {
  // string-typed for UX; "" means "use tier default" (clears override).
  max_revisions: string;
  max_revision_days: string;
}

function capDisplay(value: number): string {
  return value === 0 ? "unlimited" : String(value);
}

function overrideToString(value: number | null): string {
  return value === null ? "" : String(value);
}

function parseOverride(input: string): number | null | "invalid" {
  const trimmed = input.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  if (!Number.isInteger(n) || n < 0) return "invalid";
  return n;
}

export function RevisionRetentionCard() {
  const { data } = useQuery({
    queryKey: ["retention"],
    queryFn: getRetention,
  });

  if (!data) return null;
  return <RetentionForm settings={data} />;
}

function RetentionForm({ settings }: { settings: RetentionSettings }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const safety = useQuery({
    queryKey: ["system-safety"],
    queryFn: getSystemSafety,
  });
  const gracePeriod = safety.data?.settings.grace_period_days ?? 0;
  const authTier = safety.data?.settings.auth_tier ?? "none";

  const [draft, setDraft] = useState<DraftState>({
    max_revisions: overrideToString(settings.override_revisions),
    max_revision_days: overrideToString(settings.override_days),
  });
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState("");

  const mutation = useMutation({
    mutationFn: updateRetention,
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["retention"] });
      qc.invalidateQueries({ queryKey: ["system-safety"] });
      setPassword("");
      setTotpCode("");
      setError("");
      setDraft({
        max_revisions: overrideToString(res.override_revisions),
        max_revision_days: overrideToString(res.override_days),
      });
      toast.success("Retention settings saved");
    },
    onError: (err) => setError(err instanceof Error ? err.message : "Failed"),
  });

  const parsedRev = parseOverride(draft.max_revisions);
  const parsedDays = parseOverride(draft.max_revision_days);
  const invalid = parsedRev === "invalid" || parsedDays === "invalid";

  const newRev = parsedRev === "invalid" ? settings.override_revisions : parsedRev;
  const newDays = parsedDays === "invalid" ? settings.override_days : parsedDays;
  const dirty =
    newRev !== settings.override_revisions || newDays !== settings.override_days;
  const loosening = isLoosening(settings, newRev, newDays);
  const needsReauth = loosening && gracePeriod > 0;

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (invalid) {
      setError("Caps must be non-negative integers (or empty for tier default).");
      return;
    }
    const patch: RetentionUpdate = {};
    if (newRev !== settings.override_revisions) patch.max_revisions = newRev;
    if (newDays !== settings.override_days) patch.max_revision_days = newDays;
    if (needsReauth) {
      patch.password = password || undefined;
      patch.totp_code = totpCode || undefined;
    }
    mutation.mutate(patch);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Revision History Retention</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Edits to journal entries and member bios capture a revision so you can
          undo. Old revisions are trimmed past these caps. Tier:{" "}
          <span className="font-medium">{user?.tier ?? "—"}</span>.
        </p>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-1">
              <Label className="text-sm">Revisions per item</Label>
              <Input
                type="number"
                min={0}
                value={draft.max_revisions}
                placeholder={`tier default: ${capDisplay(settings.tier_max_revisions)}`}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, max_revisions: e.target.value }))
                }
              />
              <p className="text-xs text-muted-foreground">
                Currently effective: {capDisplay(settings.effective_max_revisions)}.
                Empty = tier default. 0 = unlimited (selfhosted only).
              </p>
            </div>
            <div className="space-y-1">
              <Label className="text-sm">Revision age (days)</Label>
              <Input
                type="number"
                min={0}
                value={draft.max_revision_days}
                placeholder={`tier default: ${capDisplay(settings.tier_max_days)}`}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, max_revision_days: e.target.value }))
                }
              />
              <p className="text-xs text-muted-foreground">
                Currently effective: {capDisplay(settings.effective_max_days)}.
                Empty = tier default. 0 = unlimited (selfhosted only).
              </p>
            </div>
          </div>
          {needsReauth && (
            <div className="space-y-3 border-t pt-3">
              <p className="text-sm text-muted-foreground">
                Lowering caps is a loosening of safety: it requires re-auth and
                takes effect after the {gracePeriod}-day grace period.
              </p>
              {(authTier === "password" || authTier === "both") && (
                <div className="space-y-1">
                  <Label className="text-sm">Password</Label>
                  <Input
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
                    <Label className="text-sm">TOTP code</Label>
                    <Input
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
                  setDraft({
                    max_revisions: overrideToString(settings.override_revisions),
                    max_revision_days: overrideToString(settings.override_days),
                  })
                }
                disabled={mutation.isPending}
              >
                Revert
              </Button>
            )}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// Mirror of split_safety_changes' retention-field rule: None = +inf for
// comparison so going from a concrete cap to None is a loosening.
function isLoosening(
  settings: RetentionSettings,
  newRev: number | null,
  newDays: number | null,
): boolean {
  return (
    isCapLoosening(settings.override_revisions, newRev) ||
    isCapLoosening(settings.override_days, newDays)
  );
}

function isCapLoosening(current: number | null, next: number | null): boolean {
  const cur = current === null ? Infinity : current === 0 ? Infinity : current;
  const nxt = next === null ? Infinity : next === 0 ? Infinity : next;
  return nxt > cur;
}
