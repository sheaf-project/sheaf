import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Pause, Pencil, Play, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { useMembers } from "@/hooks/use-members";
import { getMySystem } from "@/lib/systems";
import { listAllChannels } from "@/lib/notifications";
import {
  DOW_BITS,
  DOW_LABELS,
  createReminder,
  deleteReminder,
  listReminders,
  updateReminder,
} from "@/lib/reminders";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { PageHeader } from "@/components/page-header";
import { PendingDeleteBadge } from "@/components/pending-delete-badge";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { isDeleteQueued } from "@/types/api";
import type {
  DestructiveConfirm,
  Member,
  Reminder,
  ReminderCreate,
  ReminderScheduleKind,
  ReminderTriggerEvent,
  ReminderTriggerType,
} from "@/types/api";

function browserTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function RemindersPage() {
  const qc = useQueryClient();
  const { data: reminders, isLoading } = useQuery({
    queryKey: ["reminders"],
    queryFn: listReminders,
  });
  const { data: channels } = useQuery({
    queryKey: ["channels", "all"],
    queryFn: listAllChannels,
  });
  const { data: members } = useMembers();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });

  const [editing, setEditing] = useState<Reminder | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Reminder | null>(null);

  const memberById = useMemo(
    () => new Map<string, Member>((members ?? []).map((m) => [m.id, m])),
    [members],
  );
  const channelById = useMemo(
    () => new Map((channels ?? []).map((c) => [c.id, c])),
    [channels],
  );

  const deleteMut = useMutation({
    mutationFn: ({
      id,
      confirm,
    }: {
      id: string;
      confirm?: DestructiveConfirm;
    }) => deleteReminder(id, confirm),
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ["reminders"] });
      setDeleting(null);
      if (isDeleteQueued(resp)) {
        toast.success(
          `Deletion queued. Will finalize after ${new Date(
            resp.finalize_after,
          ).toLocaleString()} unless cancelled.`,
        );
      } else {
        toast.success("Reminder deleted");
      }
    },
  });

  const enabledMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateReminder(id, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reminders"] }),
  });

  return (
    <>
      <PageHeader title="Reminders">
        <Button
          onClick={() => setCreating(true)}
          disabled={!channels || channels.length === 0}
        >
          New reminder
        </Button>
      </PageHeader>

      <p className="mb-6 max-w-2xl text-sm text-muted-foreground">
        Reminders ride your existing notification channels — pick a channel
        when creating one. Automated reminders fire after a member fronts;
        repeated reminders run on a schedule, optionally only when a
        specific member is around.
      </p>

      {channels && channels.length === 0 && (
        <Card className="mb-6 max-w-xl">
          <CardContent className="py-6 text-sm text-muted-foreground">
            You haven&apos;t set up any notification channels yet. Create one
            on the <a href="/notifications" className="underline">Notifications</a>{" "}
            page first, then come back here to add reminders that ride it.
          </CardContent>
        </Card>
      )}

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      ) : !reminders || reminders.length === 0 ? (
        <p className="text-sm text-muted-foreground">No reminders yet.</p>
      ) : (
        <div className="space-y-3">
          {reminders.map((r) => (
            <ReminderRow
              key={r.id}
              reminder={r}
              memberById={memberById}
              channelName={channelById.get(r.channel_id)?.name ?? "Unknown channel"}
              onEdit={() => setEditing(r)}
              onDelete={() => setDeleting(r)}
              onToggle={(v) => enabledMut.mutate({ id: r.id, enabled: v })}
            />
          ))}
        </div>
      )}

      {(creating || editing) && (
        // The key forces a fresh mount when switching between rows, so
        // useState initialisers re-run with the new `initial`. Cleaner
        // than a useEffect+setState reset pattern.
        <ReminderDialog
          key={editing?.id ?? "new"}
          open
          initial={editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          channels={channels ?? []}
          members={members ?? []}
        />
      )}

      <DestructiveConfirmDialog
        open={!!deleting}
        onOpenChange={(open) => !open && setDeleting(null)}
        title="Delete reminder"
        description={
          deleting
            ? `Delete reminder "${deleting.name}"? This stops it firing immediately.`
            : ""
        }
        tier={system?.delete_confirmation ?? "none"}
        loading={deleteMut.isPending}
        onConfirm={(confirm) =>
          deleting && deleteMut.mutate({ id: deleting.id, confirm })
        }
      />
    </>
  );
}

function ReminderRow({
  reminder,
  memberById,
  channelName,
  onEdit,
  onDelete,
  onToggle,
}: {
  reminder: Reminder;
  memberById: Map<string, Member>;
  channelName: string;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: (v: boolean) => void;
}) {
  const summary = useMemo(
    () => triggerSummary(reminder, memberById),
    [reminder, memberById],
  );
  const nextFire = reminder.next_fire_at
    ? new Date(reminder.next_fire_at).toLocaleString()
    : null;
  return (
    <div
      className={cn(
        "rounded-md border p-4",
        reminder.pending_delete_at && "opacity-60",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <Bell className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="font-medium">{reminder.name}</span>
            {!reminder.enabled && (
              <Badge variant="outline" className="text-xs">
                Disabled
              </Badge>
            )}
            {reminder.pending_count > 0 && (
              <Badge variant="secondary" className="text-xs">
                {reminder.pending_count} queued
              </Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground mt-1">{summary}</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            via {channelName}
            {nextFire && ` · next ${nextFire}`}
          </p>
          <PendingDeleteBadge
            finalizeAt={reminder.pending_delete_at}
            className="mt-2"
          />
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onToggle(!reminder.enabled)}
            title={reminder.enabled ? "Pause" : "Resume"}
          >
            {reminder.enabled ? (
              <>
                <Pause className="mr-1 h-4 w-4" /> Pause
              </>
            ) : (
              <>
                <Play className="mr-1 h-4 w-4" /> Resume
              </>
            )}
          </Button>
          <Button variant="ghost" size="icon" onClick={onEdit} title="Edit">
            <Pencil className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={onDelete}
            title="Delete"
            className="text-destructive-foreground"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function triggerSummary(
  reminder: Reminder,
  memberById: Map<string, Member>,
): string {
  if (reminder.trigger_type === "automated") {
    const member =
      reminder.trigger_member_id !== null
        ? memberById.get(reminder.trigger_member_id)?.name ?? "Unknown"
        : "anyone";
    const event =
      reminder.trigger_event === "stop"
        ? "stops fronting"
        : reminder.trigger_event === "any"
          ? "fronts or stops"
          : "fronts";
    const delay = formatDuration(reminder.delay_seconds ?? 0);
    return `Fires ${delay} after ${member} ${event}`;
  }
  if (reminder.cron_expression) {
    return `Cron: ${reminder.cron_expression} (${reminder.schedule_tz ?? "UTC"})`;
  }
  const time = reminder.schedule_time ?? "??:??";
  if (reminder.schedule_kind === "daily") {
    return `Daily at ${time} (${reminder.schedule_tz ?? "UTC"})`;
  }
  if (reminder.schedule_kind === "weekly") {
    const days = DOW_LABELS.filter(
      (_, i) => (reminder.schedule_dow_mask ?? 0) & DOW_BITS[i],
    ).join(", ");
    return `Weekly ${days || "(no days set)"} at ${time}`;
  }
  if (reminder.schedule_kind === "monthly") {
    return `Day ${reminder.schedule_dom} of every month at ${time}`;
  }
  return "No schedule";
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 86400) {
    const hours = seconds / 3600;
    return hours === Math.round(hours)
      ? `${hours} h`
      : `${hours.toFixed(1)} h`;
  }
  return `${Math.round(seconds / 86400)} d`;
}

// ---------------------------------------------------------------------------
// Create / edit dialog
// ---------------------------------------------------------------------------

function ReminderDialog({
  open,
  initial,
  channels,
  members,
  onClose,
}: {
  open: boolean;
  initial: Reminder | null;
  channels: { id: string; name: string }[];
  members: Member[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(initial?.name ?? "");
  const [title, setTitle] = useState(initial?.title ?? "");
  const [body, setBody] = useState(initial?.body ?? "");
  const [channelId, setChannelId] = useState(
    initial?.channel_id ?? channels[0]?.id ?? "",
  );
  const [triggerType, setTriggerType] = useState<ReminderTriggerType>(
    initial?.trigger_type ?? "repeated",
  );

  // Automated state
  const [triggerMemberId, setTriggerMemberId] = useState<string>(
    initial?.trigger_member_id ?? "",
  );
  const [triggerEvent, setTriggerEvent] = useState<ReminderTriggerEvent>(
    initial?.trigger_event ?? "start",
  );
  const [delaySeconds, setDelaySeconds] = useState<number>(
    initial?.delay_seconds ?? 1800,
  );

  // Repeated state
  const [advanced, setAdvanced] = useState<boolean>(!!initial?.cron_expression);
  const [scheduleKind, setScheduleKind] = useState<ReminderScheduleKind>(
    initial?.schedule_kind ?? "daily",
  );
  const [scheduleTime, setScheduleTime] = useState(
    initial?.schedule_time ?? "09:00",
  );
  const [dowMask, setDowMask] = useState<number>(
    initial?.schedule_dow_mask ?? 0,
  );
  const [scheduleDom, setScheduleDom] = useState<number>(
    initial?.schedule_dom ?? 1,
  );
  const [tz, setTz] = useState(initial?.schedule_tz ?? browserTz());
  const [cronExpression, setCronExpression] = useState(
    initial?.cron_expression ?? "",
  );

  // Scoping
  const [scope, setScope] = useState<"system" | "member">(
    initial?.scope ?? "system",
  );
  const [scopeMemberIds, setScopeMemberIds] = useState<string[]>(
    initial?.scope_member_ids ?? [],
  );
  const [digestWhenAbsent, setDigestWhenAbsent] = useState<boolean>(
    initial?.digest_when_absent ?? true,
  );

  const saveMut = useMutation({
    mutationFn: async () => {
      const payload: ReminderCreate = {
        channel_id: channelId,
        name,
        title,
        body: body || null,
        trigger_type: triggerType,
        trigger_member_id:
          triggerType === "automated" ? triggerMemberId || null : null,
        trigger_event: triggerType === "automated" ? triggerEvent : null,
        delay_seconds: triggerType === "automated" ? delaySeconds : null,
        schedule_kind:
          triggerType === "repeated" && !advanced ? scheduleKind : null,
        schedule_time:
          triggerType === "repeated" && !advanced ? scheduleTime : null,
        schedule_dow_mask:
          triggerType === "repeated" && !advanced && scheduleKind === "weekly"
            ? dowMask
            : null,
        schedule_dom:
          triggerType === "repeated" && !advanced && scheduleKind === "monthly"
            ? scheduleDom
            : null,
        schedule_tz: triggerType === "repeated" ? tz : null,
        cron_expression:
          triggerType === "repeated" && advanced ? cronExpression : null,
        scope: triggerType === "repeated" ? scope : "system",
        scope_member_ids:
          triggerType === "repeated" && scope === "member" ? scopeMemberIds : [],
        digest_when_absent: digestWhenAbsent,
      };
      if (initial) {
        return updateReminder(initial.id, payload);
      }
      return createReminder(payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reminders"] });
      toast.success(initial ? "Reminder updated" : "Reminder created");
      onClose();
    },
  });

  const realMembers = members.filter((m) => !m.is_custom_front);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{initial ? "Edit reminder" : "New reminder"}</DialogTitle>
          <DialogDescription>
            Reminders deliver through a notification channel you've already set up.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="r-name">Name</Label>
            <Input
              id="r-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Daily meds"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="r-channel">Send via</Label>
            <Select value={channelId} onValueChange={setChannelId}>
              <SelectTrigger id="r-channel">
                <SelectValue placeholder="Select a channel" />
              </SelectTrigger>
              <SelectContent>
                {channels.map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="r-title">Title</Label>
              <Input
                id="r-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Example title"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="reminder-trigger-type">Trigger type</Label>
              <Select
                value={triggerType}
                onValueChange={(v) => setTriggerType(v as ReminderTriggerType)}
              >
                <SelectTrigger id="reminder-trigger-type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="repeated">Schedule</SelectItem>
                  <SelectItem value="automated">After a switch</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="r-body">Body (optional)</Label>
            <textarea
              id="r-body"
              className="w-full rounded-md border bg-background p-2 text-sm"
              rows={2}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Example reminder text"
            />
          </div>

          {triggerType === "automated" ? (
            <AutomatedSection
              members={realMembers}
              triggerMemberId={triggerMemberId}
              setTriggerMemberId={setTriggerMemberId}
              triggerEvent={triggerEvent}
              setTriggerEvent={setTriggerEvent}
              delaySeconds={delaySeconds}
              setDelaySeconds={setDelaySeconds}
            />
          ) : (
            <RepeatedSection
              advanced={advanced}
              setAdvanced={setAdvanced}
              scheduleKind={scheduleKind}
              setScheduleKind={setScheduleKind}
              scheduleTime={scheduleTime}
              setScheduleTime={setScheduleTime}
              dowMask={dowMask}
              setDowMask={setDowMask}
              scheduleDom={scheduleDom}
              setScheduleDom={setScheduleDom}
              tz={tz}
              setTz={setTz}
              cronExpression={cronExpression}
              setCronExpression={setCronExpression}
              members={realMembers}
              scope={scope}
              setScope={setScope}
              scopeMemberIds={scopeMemberIds}
              setScopeMemberIds={setScopeMemberIds}
              digestWhenAbsent={digestWhenAbsent}
              setDigestWhenAbsent={setDigestWhenAbsent}
            />
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => saveMut.mutate()}
            disabled={
              !name || !title || !channelId || saveMut.isPending
            }
          >
            {saveMut.isPending ? "Saving…" : initial ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AutomatedSection(props: {
  members: Member[];
  triggerMemberId: string;
  setTriggerMemberId: (v: string) => void;
  triggerEvent: ReminderTriggerEvent;
  setTriggerEvent: (v: ReminderTriggerEvent) => void;
  delaySeconds: number;
  setDelaySeconds: (v: number) => void;
}) {
  const minutes = Math.round(props.delaySeconds / 60);
  return (
    <div className="space-y-3 rounded-md border p-3 bg-muted/20">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="reminder-when-member">When</Label>
          <Select
            value={props.triggerMemberId || "_any"}
            onValueChange={(v) =>
              props.setTriggerMemberId(v === "_any" ? "" : v)
            }
          >
            <SelectTrigger id="reminder-when-member">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="_any">any member</SelectItem>
              {props.members.map((m) => (
                <SelectItem key={m.id} value={m.id}>
                  {m.display_name || m.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="reminder-when-event">Event</Label>
          <Select
            value={props.triggerEvent}
            onValueChange={(v) =>
              props.setTriggerEvent(v as ReminderTriggerEvent)
            }
          >
            <SelectTrigger id="reminder-when-event">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="start">starts fronting</SelectItem>
              <SelectItem value="stop">stops fronting</SelectItem>
              <SelectItem value="any">fronts or stops</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="r-delay">Fire after (minutes)</Label>
        <Input
          id="r-delay"
          type="number"
          min={0}
          max={10080}
          value={minutes}
          onChange={(e) =>
            props.setDelaySeconds(Math.max(0, Number(e.target.value)) * 60)
          }
        />
        <p className="text-xs text-muted-foreground">
          The reminder will fire {minutes} minute{minutes === 1 ? "" : "s"} after
          the matching front change.
        </p>
      </div>
    </div>
  );
}

function RepeatedSection(props: {
  advanced: boolean;
  setAdvanced: (v: boolean) => void;
  scheduleKind: ReminderScheduleKind;
  setScheduleKind: (v: ReminderScheduleKind) => void;
  scheduleTime: string;
  setScheduleTime: (v: string) => void;
  dowMask: number;
  setDowMask: (v: number) => void;
  scheduleDom: number;
  setScheduleDom: (v: number) => void;
  tz: string;
  setTz: (v: string) => void;
  cronExpression: string;
  setCronExpression: (v: string) => void;
  members: Member[];
  scope: "system" | "member";
  setScope: (v: "system" | "member") => void;
  scopeMemberIds: string[];
  setScopeMemberIds: (v: string[]) => void;
  digestWhenAbsent: boolean;
  setDigestWhenAbsent: (v: boolean) => void;
}) {
  const tzOptions = useMemo(() => {
    try {
      return (Intl as unknown as { supportedValuesOf?: (k: string) => string[] })
        .supportedValuesOf?.("timeZone") ?? [props.tz];
    } catch {
      return [props.tz];
    }
  }, [props.tz]);

  return (
    <div className="space-y-3 rounded-md border p-3 bg-muted/20">
      <div className="flex items-center justify-between">
        <Label>Schedule</Label>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input
            type="checkbox"
            checked={props.advanced}
            onChange={(e) => props.setAdvanced(e.target.checked)}
          />
          Advanced (cron)
        </label>
      </div>

      {props.advanced ? (
        <div className="space-y-1.5">
          <Input
            value={props.cronExpression}
            onChange={(e) => props.setCronExpression(e.target.value)}
            placeholder="0 9 * * 1"
            spellCheck={false}
          />
          <p className="text-xs text-muted-foreground">
            Standard 5-field cron syntax (minute, hour, day-of-month, month,
            day-of-week). Example: <code>0 9 * * 1</code> = Mondays at 09:00.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="reminder-frequency">Frequency</Label>
              <Select
                value={props.scheduleKind}
                onValueChange={(v) =>
                  props.setScheduleKind(v as ReminderScheduleKind)
                }
              >
                <SelectTrigger id="reminder-frequency">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="daily">Daily</SelectItem>
                  <SelectItem value="weekly">Weekly</SelectItem>
                  <SelectItem value="monthly">Monthly</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="reminder-time-of-day">Time of day</Label>
              <Input
                id="reminder-time-of-day"
                type="time"
                value={props.scheduleTime}
                onChange={(e) => props.setScheduleTime(e.target.value)}
              />
            </div>
          </div>

          {props.scheduleKind === "weekly" && (
            <div className="space-y-1.5">
              <Label>Days</Label>
              <div className="flex flex-wrap gap-1.5">
                {DOW_LABELS.map((label, i) => {
                  const bit = DOW_BITS[i];
                  const on = (props.dowMask & bit) !== 0;
                  return (
                    <button
                      key={label}
                      type="button"
                      onClick={() =>
                        props.setDowMask(
                          on ? props.dowMask & ~bit : props.dowMask | bit,
                        )
                      }
                      className={`rounded-md border px-2 py-1 text-xs ${
                        on
                          ? "bg-primary text-primary-foreground border-primary"
                          : "bg-background"
                      }`}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {props.scheduleKind === "monthly" && (
            <div className="space-y-1.5">
              <Label htmlFor="reminder-day-of-month">Day of month</Label>
              <Input
                id="reminder-day-of-month"
                type="number"
                min={1}
                max={31}
                value={props.scheduleDom}
                onChange={(e) =>
                  props.setScheduleDom(
                    Math.min(31, Math.max(1, Number(e.target.value))),
                  )
                }
              />
            </div>
          )}
        </div>
      )}

      <div className="space-y-1.5">
        <Label htmlFor="reminder-timezone">Timezone</Label>
        <Select value={props.tz} onValueChange={props.setTz}>
          <SelectTrigger id="reminder-timezone">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="max-h-72">
            {tzOptions.map((opt) => (
              <SelectItem key={opt} value={opt}>
                {opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="border-t pt-3 space-y-2">
        <Label htmlFor="reminder-scope">Only fire when…</Label>
        <Select
          value={props.scope}
          onValueChange={(v) => props.setScope(v as "system" | "member")}
        >
          <SelectTrigger id="reminder-scope">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="system">always (system-wide)</SelectItem>
            <SelectItem value="member">a specific member is fronting</SelectItem>
          </SelectContent>
        </Select>

        {props.scope === "member" && (
          <>
            <div className="flex flex-wrap gap-1.5">
              {props.members.map((m) => {
                const on = props.scopeMemberIds.includes(m.id);
                return (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() =>
                      props.setScopeMemberIds(
                        on
                          ? props.scopeMemberIds.filter((id) => id !== m.id)
                          : [...props.scopeMemberIds, m.id],
                      )
                    }
                    className={`rounded-md border px-2 py-1 text-xs ${
                      on
                        ? "bg-primary text-primary-foreground border-primary"
                        : "bg-background"
                    }`}
                  >
                    {m.display_name || m.name}
                  </button>
                );
              })}
            </div>
            <label className="flex items-start gap-2 text-xs cursor-pointer pt-1">
              <input
                type="checkbox"
                checked={props.digestWhenAbsent}
                onChange={(e) => props.setDigestWhenAbsent(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                If nobody on the list is fronting at the trigger time, queue the
                reminder and send it as a digest when one of them next starts
                fronting (capped at 5 missed firings).
              </span>
            </label>
          </>
        )}
      </div>
    </div>
  );
}
