import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useAuth } from "@/hooks/use-auth";
import {
  cancelPendingAction,
  cancelPendingChange,
  getSystemSafety,
  updateSystemSafety,
} from "@/lib/system-safety";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { PendingActionRow } from "@/components/pending-action-row";
import type {
  DeleteConfirmation,
  PendingAction,
  SafetyChangeRequest,
  SystemSafetySettings,
  SystemSafetyUpdate,
} from "@/types/api";

const categoryLabels: {
  key: keyof SystemSafetySettings;
  label: string;
  desc: string;
}[] = [
  { key: "applies_to_members", label: "Members", desc: "Deleting a member" },
  { key: "applies_to_groups", label: "Groups", desc: "Deleting a group" },
  { key: "applies_to_tags", label: "Tags", desc: "Deleting a tag" },
  { key: "applies_to_fields", label: "Custom fields", desc: "Deleting a custom field" },
  { key: "applies_to_fronts", label: "Front entries", desc: "Deleting a front entry" },
  { key: "applies_to_journals", label: "Journal entries", desc: "Deleting a journal entry" },
  { key: "applies_to_images", label: "Images", desc: "Deleting an uploaded image" },
  { key: "applies_to_revisions", label: "Revision pins", desc: "Unpinning a protected revision" },
  {
    key: "applies_to_notifications",
    label: "Notifications",
    desc: "Deleting a channel or revoking a watcher",
  },
  {
    key: "applies_to_reminders",
    label: "Reminders",
    desc: "Deleting a reminder",
  },
  {
    key: "applies_to_polls",
    label: "Polls",
    desc: "Deleting a poll (with its votes and audit log)",
  },
];

function changeSummary(changes: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(changes)) {
    if (k === "safety_grace_period_days") {
      parts.push(`grace period → ${v} days`);
    } else if (k === "delete_confirmation") {
      parts.push(`auth tier → ${v}`);
    } else if (k.startsWith("safety_applies_to_")) {
      const cat = k.replace("safety_applies_to_", "");
      parts.push(`disable ${cat}`);
    } else if (k === "auto_pin_first_revision") {
      parts.push(v === false ? "disable auto-pin first revision" : "enable auto-pin first revision");
    } else if (k === "journal_max_revisions") {
      parts.push(
        v === null ? "revision count cap → tier default" : `revision count cap → ${v}`,
      );
    } else if (k === "journal_max_revision_days") {
      parts.push(
        v === null ? "revision age cap → tier default" : `revision age cap → ${v} days`,
      );
    } else {
      parts.push(`${k} → ${v}`);
    }
  }
  return parts.join(", ");
}

function timeRemaining(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "finalizing…";
  const hours = Math.ceil(ms / 3_600_000);
  if (hours < 24) return `in ${hours}h`;
  const days = Math.ceil(ms / 86_400_000);
  return `in ${days} day${days === 1 ? "" : "s"}`;
}

export function SystemSafetyCard() {
  const { data } = useQuery({
    queryKey: ["system-safety"],
    queryFn: getSystemSafety,
  });

  if (!data) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">System Safety</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Optional grace periods and re-auth for destructive actions. Tightening
          applies immediately; loosening waits the current grace period before
          taking effect.
        </p>
        <SafetyForm settings={data.settings} />
        {data.pending_actions.length > 0 && (
          <>
            <Separator />
            <PendingActionsList actions={data.pending_actions} />
          </>
        )}
        {data.pending_changes.length > 0 && (
          <>
            <Separator />
            <PendingChangesList changes={data.pending_changes} />
          </>
        )}
      </CardContent>
    </Card>
  );
}

function SafetyForm({ settings }: { settings: SystemSafetySettings }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [draft, setDraft] = useState<SystemSafetySettings>(settings);
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState("");

  const mutation = useMutation({
    mutationFn: updateSystemSafety,
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["system-safety"] });
      qc.invalidateQueries({ queryKey: ["system", "me"] });
      setPassword("");
      setTotpCode("");
      setError("");
      if (res.deferred.length > 0) {
        toast.success(
          `Queued: ${res.deferred.join(", ")}. Finalizes after the current grace period.`,
        );
      } else {
        toast.success("Safety settings saved");
      }
    },
    onError: (err) => setError(err instanceof Error ? err.message : "Failed"),
  });

  const dirty = hasDiff(settings, draft);
  const loosening = detectLoosening(settings, draft);
  const needsReauth =
    loosening && (settings.grace_period_days > 0 || draft.grace_period_days > 0);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    const patch: SystemSafetyUpdate = diffFields(settings, draft);
    if (needsReauth) {
      patch.password = password || undefined;
      patch.totp_code = totpCode || undefined;
    }
    mutation.mutate(patch);
  }

  function setField<K extends keyof SystemSafetySettings>(
    key: K,
    value: SystemSafetySettings[K],
  ) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-1">
          <Label className="text-sm">Grace period (days)</Label>
          <Input
            type="number"
            min={0}
            max={365}
            value={draft.grace_period_days}
            onChange={(e) =>
              setField("grace_period_days", Number(e.target.value) || 0)
            }
          />
          <p className="text-xs text-muted-foreground">
            0 disables the grace period entirely.
          </p>
        </div>
        <div className="space-y-1">
          <Label className="text-sm">Auth tier</Label>
          <Select
            value={draft.auth_tier}
            onValueChange={(v) => setField("auth_tier", v as DeleteConfirmation)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">No confirmation</SelectItem>
              <SelectItem value="password">Require password</SelectItem>
              {user?.totp_enabled && (
                <SelectItem value="totp">Require TOTP</SelectItem>
              )}
              {user?.totp_enabled && (
                <SelectItem value="both">Password + TOTP</SelectItem>
              )}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            Required for safeguarded destructive actions.
          </p>
        </div>
      </div>
      <div className="space-y-2">
        <Label className="text-sm">Apply safety to</Label>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {categoryLabels.map(({ key, label, desc }) => (
            <label
              key={key}
              className="flex items-start gap-2 text-sm cursor-pointer"
            >
              <Checkbox
                checked={draft[key] as boolean}
                onCheckedChange={(v) => setField(key, Boolean(v))}
                className="mt-0.5"
              />
              <span>
                <span className="font-medium">{label}</span>
                <span className="block text-xs text-muted-foreground">
                  {desc}
                </span>
              </span>
            </label>
          ))}
        </div>
      </div>
      <div className="space-y-2 border-t pt-3">
        <Label className="text-sm">Revision pinning</Label>
        <label className="flex items-start gap-2 text-sm cursor-pointer">
          <Checkbox
            checked={draft.auto_pin_first_revision}
            onCheckedChange={(v) => setField("auto_pin_first_revision", Boolean(v))}
            className="mt-0.5"
          />
          <span>
            <span className="font-medium">Auto-pin original revision</span>
            <span className="block text-xs text-muted-foreground">
              When a journal entry or member bio is first edited, automatically
              pin the captured original. Pinned revisions are exempt from the
              rolling history cap.
            </span>
          </span>
        </label>
      </div>
      {needsReauth && (
        <div className="space-y-3 border-t pt-3">
          <p className="text-sm text-muted-foreground">
            Loosening safety requires re-auth and will take effect after the
            current grace period.
          </p>
          {(settings.auth_tier === "password" || settings.auth_tier === "both") && (
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
          {(settings.auth_tier === "totp" || settings.auth_tier === "both") &&
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
        <Button type="submit" size="sm" disabled={!dirty || mutation.isPending}>
          {mutation.isPending ? "Saving…" : "Save"}
        </Button>
        {dirty && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setDraft(settings)}
            disabled={mutation.isPending}
          >
            Revert
          </Button>
        )}
      </div>
    </form>
  );
}

function PendingActionsList({ actions }: { actions: PendingAction[] }) {
  const qc = useQueryClient();
  const cancel = useMutation({
    mutationFn: cancelPendingAction,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system-safety"] });
      toast.success("Cancelled");
    },
  });

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-medium">Pending destructive actions</h4>
      <div className="space-y-2">
        {actions.map((a) => (
          <PendingActionRow
            key={a.id}
            action={a}
            onCancel={() => cancel.mutate(a.id)}
            cancelling={cancel.isPending && cancel.variables === a.id}
          />
        ))}
      </div>
    </div>
  );
}

function PendingChangesList({ changes }: { changes: SafetyChangeRequest[] }) {
  const qc = useQueryClient();
  const cancel = useMutation({
    mutationFn: cancelPendingChange,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system-safety"] });
      toast.success("Cancelled");
    },
  });

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-medium">Pending safety changes</h4>
      {changes.map((c) => (
        <div
          key={c.id}
          className="flex items-start gap-3 rounded-md border px-3 py-2 text-sm"
        >
          <div className="flex-1 min-w-0 space-y-0.5">
            <div className="font-medium">{changeSummary(c.changes)}</div>
            <div className="text-xs text-muted-foreground">
              Finalizes {timeRemaining(c.finalize_after)}
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => cancel.mutate(c.id)}
            disabled={cancel.isPending && cancel.variables === c.id}
          >
            {cancel.isPending && cancel.variables === c.id
              ? "Cancelling…"
              : "Cancel"}
          </Button>
        </div>
      ))}
    </div>
  );
}

const CATEGORY_KEYS = [
  "applies_to_members",
  "applies_to_groups",
  "applies_to_tags",
  "applies_to_fields",
  "applies_to_fronts",
  "applies_to_journals",
  "applies_to_images",
  "applies_to_revisions",
  "applies_to_notifications",
  "applies_to_reminders",
  "applies_to_polls",
] as const;

function hasDiff(a: SystemSafetySettings, b: SystemSafetySettings): boolean {
  if (a.grace_period_days !== b.grace_period_days) return true;
  if (a.auth_tier !== b.auth_tier) return true;
  if (a.auto_pin_first_revision !== b.auto_pin_first_revision) return true;
  return CATEGORY_KEYS.some((k) => a[k] !== b[k]);
}

function diffFields(
  current: SystemSafetySettings,
  draft: SystemSafetySettings,
): SystemSafetyUpdate {
  const patch: SystemSafetyUpdate = {};
  if (current.grace_period_days !== draft.grace_period_days)
    patch.grace_period_days = draft.grace_period_days;
  if (current.auth_tier !== draft.auth_tier) patch.auth_tier = draft.auth_tier;
  if (current.auto_pin_first_revision !== draft.auto_pin_first_revision)
    patch.auto_pin_first_revision = draft.auto_pin_first_revision;
  for (const key of CATEGORY_KEYS) {
    if (current[key] !== draft[key]) patch[key] = draft[key];
  }
  return patch;
}

const tierStrength: Record<DeleteConfirmation, number> = {
  none: 0,
  password: 1,
  totp: 1,
  both: 2,
};

function detectLoosening(
  current: SystemSafetySettings,
  draft: SystemSafetySettings,
): boolean {
  if (draft.grace_period_days < current.grace_period_days) return true;
  if (tierStrength[draft.auth_tier] < tierStrength[current.auth_tier]) return true;
  if (current.auto_pin_first_revision && !draft.auto_pin_first_revision) return true;
  for (const key of CATEGORY_KEYS) {
    if (current[key] && !draft[key]) return true;
  }
  return false;
}
