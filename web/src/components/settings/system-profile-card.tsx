import { type FormEvent, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getMySystem, updateMySystem } from "@/lib/systems";
import { AvatarUpload } from "@/components/avatar-upload";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { dateFormatLabels } from "@/lib/date-format";
import type { DateFormat, PrivacyLevel } from "@/types/api";
import { toast } from "sonner";

export function SystemProfileCard() {
  const qc = useQueryClient();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const update = useMutation({
    mutationFn: updateMySystem,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "me"] });
      toast.success("System settings saved");
    },
  });

  if (!system) return null;

  return (
    <SystemSettingsForm
      key={system.id}
      initial={system}
      onSubmit={(data) => update.mutate(data)}
      loading={update.isPending}
    />
  );
}

function SystemSettingsForm({
  initial,
  onSubmit,
  loading,
}: {
  initial: { name: string; description: string | null; note: string | null; tag: string | null; avatar_url: string | null; color: string | null; privacy: PrivacyLevel; date_format?: DateFormat };
  onSubmit: (data: { name: string; description: string | null; note: string | null; tag: string | null; avatar_url: string | null; color: string | null; privacy: PrivacyLevel; date_format: DateFormat }) => void;
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
          <Button type="submit" disabled={loading}>
            {loading ? "Saving..." : "Save"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
