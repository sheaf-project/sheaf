import { type FormEvent, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getMySystem, updateMySystem } from "@/lib/systems";
import { AvatarUpload } from "@/components/avatar-upload";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  AUTO_VALUE,
  FOLLOW_ACCOUNT_VALUE,
  TimezoneSelect,
} from "@/components/timezone-select";
import { useTimezone } from "@/hooks/use-timezone";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { dateFormatLabels } from "@/lib/date-format";
import { isStepUpRequiredError, showApiErrorToast } from "@/lib/api-errors";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import type {
  DateFormat,
  DestructiveConfirm,
  PrivacyLevel,
  SystemUpdate,
} from "@/types/api";
import { toast } from "sonner";

export function SystemProfileCard() {
  const qc = useQueryClient();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  // Raising the system to Public is a safeguarded exposure like any other, so
  // the save can come back asking for a password or 2FA code. Hold the data the
  // form submitted and re-send it with the step-up credentials once confirmed.
  const [pendingRaise, setPendingRaise] = useState<SystemUpdate | null>(null);
  const update = useMutation({
    mutationFn: ({
      data,
      skipErrorToast = false,
    }: {
      data: SystemUpdate;
      skipErrorToast?: boolean;
    }) => updateMySystem(data, skipErrorToast),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "me"] });
      toast.success("System settings saved");
    },
  });

  if (!system) return null;

  function save(data: SystemUpdate) {
    update.mutate(
      { data, skipErrorToast: true },
      {
        onError: (err) => {
          if (isStepUpRequiredError(err)) {
            setPendingRaise(data);
            return;
          }
          showApiErrorToast(err, "Couldn't save system settings.", {
            force: true,
          });
        },
      },
    );
  }

  return (
    <>
      <SystemSettingsForm
        key={system.id}
        initial={system}
        onSubmit={save}
        loading={update.isPending}
      />
      <DestructiveConfirmDialog
        open={!!pendingRaise}
        onOpenChange={(open) => !open && setPendingRaise(null)}
        title="Confirm making your system public"
        description="Setting your system to Public exposes it through your shared views and links. Confirm now; if you have a grace period set, it takes effect after your System Safety window."
        tier={system.delete_confirmation}
        actionLabel="Confirm"
        actionLabelLoading="Saving..."
        loading={update.isPending}
        onConfirm={(confirm?: DestructiveConfirm) => {
          if (!pendingRaise) return;
          update.mutate(
            { data: { ...pendingRaise, ...confirm } },
            { onSuccess: () => setPendingRaise(null) },
          );
        }}
      />
    </>
  );
}

function SystemSettingsForm({
  initial,
  onSubmit,
  loading,
}: {
  initial: { name: string; description: string | null; note: string | null; tag: string | null; avatar_url: string | null; color: string | null; privacy: PrivacyLevel; pending_privacy?: PrivacyLevel | null; privacy_activates_at?: string | null; date_format?: DateFormat; timezone?: string | null; show_member_created_date?: boolean };
  onSubmit: (data: { name: string; description: string | null; note: string | null; tag: string | null; avatar_url: string | null; color: string | null; privacy: PrivacyLevel; date_format: DateFormat; timezone: string | null; show_member_created_date: boolean }) => void;
  loading: boolean;
}) {
  const [name, setName] = useState(initial.name);
  const [avatarUrl, setAvatarUrl] = useState(initial.avatar_url);
  const [description, setDescription] = useState(initial.description ?? "");
  const [note, setNote] = useState(initial.note ?? "");
  const [tag, setTag] = useState(initial.tag ?? "");
  const [color, setColor] = useState(initial.color ?? "");
  const [privacy, setPrivacy] = useState<PrivacyLevel>(initial.privacy);
  const [dateFormat, setDateFormat] = useState<DateFormat>(initial.date_format ?? "ymd");
  const [timezone, setTimezone] = useState<string | null>(initial.timezone ?? null);
  const [showMemberCreatedDate, setShowMemberCreatedDate] = useState<boolean>(
    initial.show_member_created_date ?? false,
  );
  const { formatDate } = useDateFormatters();

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      name,
      avatar_url: avatarUrl,
      description: description || null,
      note: note || null,
      tag: tag || null,
      color: color || null,
      privacy,
      date_format: dateFormat,
      timezone,
      show_member_created_date: showMemberCreatedDate,
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">System profile</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <AvatarUpload
            url={avatarUrl}
            fallback={name.charAt(0).toUpperCase() || "?"}
            onUpload={setAvatarUrl}
            onRemove={() => setAvatarUrl(null)}
          />
          <div className="space-y-2">
            <Label htmlFor="system-name">Name</Label>
            <Input id="system-name" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="system-description">Description</Label>
            <Input
              id="system-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="system-note">Notes</Label>
            <textarea
              id="system-note"
              className="w-full rounded-md border bg-background p-2 text-sm font-mono"
              rows={4}
              maxLength={5000}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Quick reference scratchpad..."
            />
            <p className="text-xs text-muted-foreground">
              Markdown supported. Edits overwrite immediately. No revision
              history, not protected by System Safety.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="system-tag">Tag</Label>
              <Input
                id="system-tag"
                value={tag}
                onChange={(e) => setTag(e.target.value)}
                placeholder="Short ID"
                maxLength={8}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="system-color">Color</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="system-color"
                  type="color"
                  value={color || "#000000"}
                  onChange={(e) => setColor(e.target.value)}
                  className="h-10 w-14 p-1"
                />
                <Input
                  value={color}
                  onChange={(e) => setColor(e.target.value)}
                  className="flex-1"
                />
              </div>
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="system-privacy">Privacy</Label>
            <Select value={privacy} onValueChange={(v) => setPrivacy(v as PrivacyLevel)}>
              <SelectTrigger id="system-privacy">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="private">Private</SelectItem>
                <SelectItem value="friends">Friends only</SelectItem>
                <SelectItem value="public">Public</SelectItem>
              </SelectContent>
            </Select>
            {initial.pending_privacy === "public" &&
              initial.privacy_activates_at && (
                <p className="text-xs text-amber-600">
                  A change to Public is waiting out your System Safety grace
                  period. It takes effect{" "}
                  {formatDate(initial.privacy_activates_at)}.
                  Until then your system stays {initial.privacy}.
                </p>
              )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="system-date-format">Date format</Label>
            <Select value={dateFormat} onValueChange={(v) => setDateFormat(v as DateFormat)}>
              <SelectTrigger id="system-date-format">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.entries(dateFormatLabels) as [DateFormat, string][]).map(([k, v]) => (
                  <SelectItem key={k} value={k}>{v}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="system-timezone">Timezone</Label>
            <TimezoneSelect
              id="system-timezone"
              value={timezone ?? AUTO_VALUE}
              specialOptions={[
                { value: AUTO_VALUE, label: "Automatic (device local)" },
              ]}
              onValueChange={(v) => setTimezone(v === AUTO_VALUE ? null : v)}
            />
            <p className="text-xs text-muted-foreground">
              The timezone timestamps are shown in, synced across your devices.
              "Automatic" uses each device's own clock. Saved with this form.
            </p>
          </div>
          <DeviceTimezoneOverride />
          <div className="flex items-start gap-3">
            <Checkbox
              id="show-member-created-date"
              checked={showMemberCreatedDate}
              onCheckedChange={(v) => setShowMemberCreatedDate(v === true)}
            />
            <div>
              <Label
                htmlFor="show-member-created-date"
                className="text-sm font-medium cursor-pointer"
              >
                Show member created dates
              </Label>
              <p className="text-xs text-muted-foreground mt-0.5">
                Show when each member was added, on their profile. Saved with
                this form.
              </p>
            </div>
          </div>
          <Button type="submit" disabled={loading}>
            {loading ? "Saving..." : "Save"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

/**
 * Per-device timezone override. Distinct from the account default above: this
 * writes localStorage on this browser only, applies immediately (no Save), and
 * never touches the synced account value - so a machine in another zone can pin
 * its own without changing what other devices see.
 */
function DeviceTimezoneOverride() {
  const { deviceOverride, setDeviceOverride, resolvedTimeZone } = useTimezone();

  // Map the override state (null | "auto" | zone) onto the picker's sentinels.
  const value =
    deviceOverride === null
      ? FOLLOW_ACCOUNT_VALUE
      : deviceOverride === "auto"
        ? AUTO_VALUE
        : deviceOverride;

  return (
    <div className="space-y-2 rounded-md border p-3 bg-muted/20">
      <Label htmlFor="device-timezone">On this device</Label>
      <TimezoneSelect
        id="device-timezone"
        value={value}
        specialOptions={[
          { value: FOLLOW_ACCOUNT_VALUE, label: "Follow account default" },
          { value: AUTO_VALUE, label: "Automatic (this device's clock)" },
        ]}
        onValueChange={(v) =>
          setDeviceOverride(
            v === FOLLOW_ACCOUNT_VALUE ? null : v === AUTO_VALUE ? "auto" : v,
          )
        }
      />
      <p className="text-xs text-muted-foreground">
        Overrides the account timezone on this browser only, applied
        immediately. Currently showing times in{" "}
        {resolvedTimeZone ?? "this device's local zone"}.
      </p>
    </div>
  );
}
