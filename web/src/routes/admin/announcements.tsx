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
  type Announcement,
} from "@/lib/announcements";
import { timeAgo } from "@/lib/utils";
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
        </div>
        <p className="text-xs text-muted-foreground line-clamp-2">
          {announcement.body}
        </p>
        <div className="mt-1 flex items-center gap-3 text-[11px] text-muted-foreground">
          <span title={new Date(announcement.created_at).toLocaleString()}>
            Created {timeAgo(announcement.created_at)}
          </span>
          {announcement.starts_at && (
            <span>Starts: {new Date(announcement.starts_at).toLocaleDateString()}</span>
          )}
          {announcement.expires_at && (
            <span>
              Expires: {new Date(announcement.expires_at).toLocaleDateString()}
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
  const [title, setTitle] = useState(announcement.title);
  const [body, setBody] = useState(announcement.body);
  const [severity, setSeverity] = useState<string>(announcement.severity);
  const [dismissible, setDismissible] = useState(announcement.dismissible);
  const [visibleWhileLoggedOut, setVisibleWhileLoggedOut] = useState(
    announcement.visible_while_logged_out,
  );

  const save = useMutation({
    mutationFn: () =>
      updateAnnouncement(announcement.id, {
        title,
        body,
        severity,
        dismissible,
        visible_while_logged_out: visibleWhileLoggedOut,
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
          <Label className="text-xs">Title</Label>
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={200}
          />
        </div>
        <div className="space-y-1 w-32">
          <Label className="text-xs">Severity</Label>
          <Select value={severity} onValueChange={setSeverity}>
            <SelectTrigger>
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
        <Label className="text-xs">Body</Label>
        <Input
          value={body}
          onChange={(e) => setBody(e.target.value)}
          maxLength={2000}
        />
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

function CreateAnnouncementForm({ onCreated }: { onCreated: () => void }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [severity, setSeverity] = useState("info");
  const [dismissible, setDismissible] = useState(true);
  const [visibleWhileLoggedOut, setVisibleWhileLoggedOut] = useState(false);

  const create = useMutation({
    mutationFn: () =>
      createAnnouncement({
        title,
        body,
        severity,
        dismissible,
        visible_while_logged_out: visibleWhileLoggedOut,
      }),
    onSuccess: () => {
      setTitle("");
      setBody("");
      setSeverity("info");
      setDismissible(true);
      setVisibleWhileLoggedOut(false);
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
