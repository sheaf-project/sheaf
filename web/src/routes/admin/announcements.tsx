import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  getAdminAnnouncements,
  createAnnouncement,
  updateAnnouncement,
  deleteAnnouncement,
  ANNOUNCEMENT_PLATFORMS,
  type Announcement,
  type AnnouncementPlatform,
} from "@/lib/announcements";
import { timeAgo } from "@/lib/utils";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import {
  Plus,
  Trash2,
  Pencil,
  Eye,
  EyeOff,
  Info,
  AlertTriangle,
  AlertOctagon,
} from "lucide-react";

const severityIcons = {
  info: Info,
  warning: AlertTriangle,
  critical: AlertOctagon,
};

const severityColors = {
  info: "bg-blue-500/10 text-blue-700 dark:text-blue-300 border-blue-500/30",
  warning:
    "bg-yellow-500/10 text-yellow-800 dark:text-yellow-200 border-yellow-500/30",
  critical: "bg-destructive/10 text-destructive border-destructive/30",
};

function AnnouncementRow({ announcement }: { announcement: Announcement }) {
  const qc = useQueryClient();
  const { formatDate, formatDateTime } = useDateFormatters();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const toggleActive = useMutation({
    mutationFn: () =>
      updateAnnouncement(announcement.id, { active: !announcement.active }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "announcements"] });
      toast.success(announcement.active ? "Deactivated" : "Activated");
    },
  });

  const remove = useMutation({
    mutationFn: () => deleteAnnouncement(announcement.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "announcements"] });
      toast.success("Announcement deleted");
    },
  });

  if (editing) {
    return (
      <EditAnnouncementForm
        announcement={announcement}
        onDone={() => setEditing(false)}
      />
    );
  }

  const SeverityIcon =
    severityIcons[announcement.severity] ?? severityIcons.info;
  const colorClass = severityColors[announcement.severity] ?? severityColors.info;

  return (
    <div className="flex items-start gap-3 border-b px-4 py-3 last:border-0">
      <SeverityIcon className="mt-0.5 h-4 w-4 shrink-0 opacity-70" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="font-medium text-sm">{announcement.title}</span>
          <Badge variant="outline" className={colorClass + " text-[10px] px-1.5 py-0"}>
            {announcement.severity}
          </Badge>
          {!announcement.active && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
              Inactive
            </Badge>
          )}
          {!announcement.dismissible && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
              Non-dismissible
            </Badge>
          )}
          {announcement.visible_while_logged_out && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
              Logged-out
            </Badge>
          )}
          {announcement.platforms.length > 0 && (
            <Badge
              variant="secondary"
              className="text-[10px] px-1.5 py-0"
              title="Only clients on these platforms see this announcement"
            >
              {announcement.platforms.map(platformLabel).join(", ")} only
            </Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground line-clamp-2">
          {announcement.body}
        </p>
        <div className="mt-1 flex items-center gap-3 text-[11px] text-muted-foreground">
          <span title={formatDateTime(announcement.created_at)}>
            Created {timeAgo(announcement.created_at)}
          </span>
          {announcement.starts_at && (
            <span>Starts: {formatDate(announcement.starts_at)}</span>
          )}
          {announcement.expires_at && (
            <span>
              Expires: {formatDateTime(announcement.expires_at)}
            </span>
          )}
        </div>
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <Button
          size="sm"
          variant="ghost"
          className="h-7 w-7 p-0"
          onClick={() => toggleActive.mutate()}
          disabled={toggleActive.isPending}
          title={announcement.active ? "Deactivate" : "Activate"}
        >
          {announcement.active ? (
            <EyeOff className="h-3.5 w-3.5" />
          ) : (
            <Eye className="h-3.5 w-3.5" />
          )}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 w-7 p-0"
          onClick={() => setEditing(true)}
          title="Edit"
        >
          <Pencil className="h-3.5 w-3.5" />
        </Button>
        {confirming ? (
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              variant="destructive"
              className="h-7 text-xs"
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
            >
              Confirm
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => setConfirming(false)}
            >
              Cancel
            </Button>
          </div>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="h-7 w-7 p-0 text-destructive hover:text-destructive"
            onClick={() => setConfirming(true)}
            title="Delete"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    </div>
  );
}

function EditAnnouncementForm({
  announcement,
  onDone,
}: {
  announcement: Announcement;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const { toDateTimeLocal, fromDateTimeLocal } = useDateFormatters();
  const [title, setTitle] = useState(announcement.title);
  const [body, setBody] = useState(announcement.body);
  const [severity, setSeverity] = useState<string>(announcement.severity);
  const [dismissible, setDismissible] = useState(announcement.dismissible);
  const [visibleWhileLoggedOut, setVisibleWhileLoggedOut] = useState(
    announcement.visible_while_logged_out,
  );
  const [platforms, setPlatforms] = useState<AnnouncementPlatform[]>(
    announcement.platforms,
  );
  const [expiresAt, setExpiresAt] = useState(
    toDateTimeLocal(announcement.expires_at),
  );

  const save = useMutation({
    mutationFn: () =>
      updateAnnouncement(announcement.id, {
        title,
        body,
        severity,
        dismissible,
        visible_while_logged_out: visibleWhileLoggedOut,
        platforms,
        ...(expiresAt
          ? { expires_at: fromDateTimeLocal(expiresAt) }
          : { clear_expires_at: true }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "announcements"] });
      toast.success("Announcement updated");
      onDone();
    },
  });

  return (
    <div className="border-b px-4 py-3 last:border-0 space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1 flex-1 min-w-48">
          <Label htmlFor="edit-title" className="text-xs">Title</Label>
          <Input
            id="edit-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={200}
          />
        </div>
        <div className="space-y-1 w-32">
          <Label htmlFor="edit-severity" className="text-xs">Severity</Label>
          <Select value={severity} onValueChange={setSeverity}>
            <SelectTrigger id="edit-severity">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="info">Info</SelectItem>
              <SelectItem value="warning">Warning</SelectItem>
              <SelectItem value="critical">Critical</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      <div className="space-y-1">
        <Label htmlFor="edit-body" className="text-xs">Body</Label>
        <Input
          id="edit-body"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          maxLength={2000}
        />
        <p className="text-xs text-muted-foreground">
          Supports markdown links, e.g. [read more](https://example.com)
        </p>
      </div>
      <div className="space-y-1">
        <Label htmlFor="edit-expires" className="text-xs">
          Expires (optional)
        </Label>
        <Input
          id="edit-expires"
          type="datetime-local"
          value={expiresAt}
          onChange={(e) => setExpiresAt(e.target.value)}
          className="w-60"
        />
        <p className="text-xs text-muted-foreground">
          Stops showing to users after this time. Leave blank to show until
          manually deactivated.
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Checkbox
          id="edit-dismissible"
          checked={dismissible}
          onCheckedChange={(c) => setDismissible(c === true)}
        />
        <Label htmlFor="edit-dismissible" className="text-xs">
          Dismissible
        </Label>
      </div>
      <div className="flex items-center gap-2">
        <Checkbox
          id="edit-logged-out"
          checked={visibleWhileLoggedOut}
          onCheckedChange={(c) => setVisibleWhileLoggedOut(c === true)}
        />
        <Label htmlFor="edit-logged-out" className="text-xs">
          Visible while logged out (shows on login page)
        </Label>
      </div>
      <PlatformPicker idPrefix="edit" value={platforms} onChange={setPlatforms} />
      <div className="flex gap-2">
        <Button
          size="sm"
          onClick={() => save.mutate()}
          disabled={save.isPending || !title.trim() || !body.trim()}
        >
          {save.isPending ? "Saving..." : "Save"}
        </Button>
        <Button size="sm" variant="outline" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function platformLabel(p: AnnouncementPlatform): string {
  return ANNOUNCEMENT_PLATFORMS.find((x) => x.value === p)?.label ?? p;
}

/**
 * Which clients an announcement is for. Nothing ticked means everyone,
 * which is what every announcement was before this existed; ticking one or
 * more narrows it to clients that identify as one of them.
 */
function PlatformPicker({
  idPrefix,
  value,
  onChange,
}: {
  idPrefix: string;
  value: AnnouncementPlatform[];
  onChange: (next: AnnouncementPlatform[]) => void;
}) {
  function toggle(p: AnnouncementPlatform, on: boolean) {
    onChange(on ? [...value.filter((x) => x !== p), p] : value.filter((x) => x !== p));
  }
  return (
    <div className="space-y-1">
      <Label className="text-xs">Platforms</Label>
      <div className="flex flex-wrap items-center gap-4">
        {ANNOUNCEMENT_PLATFORMS.map((p) => (
          <div key={p.value} className="flex items-center gap-2">
            <Checkbox
              id={`${idPrefix}-platform-${p.value}`}
              checked={value.includes(p.value)}
              onCheckedChange={(c) => toggle(p.value, c === true)}
            />
            <Label htmlFor={`${idPrefix}-platform-${p.value}`} className="text-xs">
              {p.label}
            </Label>
          </div>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        {value.length === 0
          ? "Shown on every client. Tick platforms to show it only there."
          : `Shown only on ${value.map(platformLabel).join(", ")}.`}
      </p>
    </div>
  );
}

function CreateAnnouncementForm({ onCreated }: { onCreated: () => void }) {
  const { fromDateTimeLocal } = useDateFormatters();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [severity, setSeverity] = useState("info");
  const [dismissible, setDismissible] = useState(true);
  const [visibleWhileLoggedOut, setVisibleWhileLoggedOut] = useState(false);
  const [platforms, setPlatforms] = useState<AnnouncementPlatform[]>([]);
  const [expiresAt, setExpiresAt] = useState("");

  const create = useMutation({
    mutationFn: () =>
      createAnnouncement({
        title,
        body,
        severity,
        dismissible,
        visible_while_logged_out: visibleWhileLoggedOut,
        platforms,
        expires_at: fromDateTimeLocal(expiresAt),
      }),
    onSuccess: () => {
      setTitle("");
      setBody("");
      setSeverity("info");
      setDismissible(true);
      setVisibleWhileLoggedOut(false);
      setPlatforms([]);
      setExpiresAt("");
      onCreated();
      toast.success("Announcement created");
    },
  });

  return (
    <Card>
      <CardContent className="pt-6 space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1 flex-1 min-w-48">
            <Label htmlFor="ann-title" className="text-xs">
              Title
            </Label>
            <Input
              id="ann-title"
              placeholder="Announcement title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
            />
          </div>
          <div className="space-y-1 w-32">
            <Label htmlFor="ann-severity" className="text-xs">
              Severity
            </Label>
            <Select value={severity} onValueChange={setSeverity}>
              <SelectTrigger id="ann-severity">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="info">Info</SelectItem>
                <SelectItem value="warning">Warning</SelectItem>
                <SelectItem value="critical">Critical</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="ann-body" className="text-xs">
            Body
          </Label>
          <Input
            id="ann-body"
            placeholder="Announcement message"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            maxLength={2000}
          />
          <p className="text-xs text-muted-foreground">
            Supports markdown links, e.g. [read more](https://example.com)
          </p>
        </div>
        <div className="space-y-1">
          <Label htmlFor="ann-expires" className="text-xs">
            Expires (optional)
          </Label>
          <Input
            id="ann-expires"
            type="datetime-local"
            value={expiresAt}
            onChange={(e) => setExpiresAt(e.target.value)}
            className="w-60"
          />
          <p className="text-xs text-muted-foreground">
            Stops showing to users after this time. Leave blank to show until
            manually deactivated.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Checkbox
            id="ann-dismissible"
            checked={dismissible}
            onCheckedChange={(c) => setDismissible(c === true)}
          />
          <Label htmlFor="ann-dismissible" className="text-xs">
            Dismissible (users can hide this)
          </Label>
        </div>
        <PlatformPicker idPrefix="ann" value={platforms} onChange={setPlatforms} />
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Checkbox
              id="ann-logged-out"
              checked={visibleWhileLoggedOut}
              onCheckedChange={(c) => setVisibleWhileLoggedOut(c === true)}
            />
            <Label htmlFor="ann-logged-out" className="text-xs">
              Visible while logged out (shows on login page)
            </Label>
          </div>
          <Button
            onClick={() => create.mutate()}
            disabled={create.isPending || !title.trim() || !body.trim()}
          >
            <Plus className="h-4 w-4 mr-1" />
            {create.isPending ? "Creating..." : "Create"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function AdminAnnouncementsPage() {
  const qc = useQueryClient();
  const { data: announcements, isLoading } = useQuery({
    queryKey: ["admin", "announcements"],
    queryFn: getAdminAnnouncements,
  });

  const count = announcements?.length ?? 0;
  const activeCount = announcements?.filter((a) => a.active).length ?? 0;

  return (
    <>
      <PageHeader title="Announcements" />
      <div className="max-w-5xl space-y-4">
        <CreateAnnouncementForm
          onCreated={() =>
            qc.invalidateQueries({ queryKey: ["admin", "announcements"] })
          }
        />

        {isLoading ? null : count === 0 ? (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No announcements yet
            </CardContent>
          </Card>
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              {count} announcement{count !== 1 ? "s" : ""} ({activeCount}{" "}
              active)
            </p>
            <Card>
              <CardContent className="p-0">
                {announcements?.map((a) => (
                  <AnnouncementRow key={a.id} announcement={a} />
                ))}
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </>
  );
}
