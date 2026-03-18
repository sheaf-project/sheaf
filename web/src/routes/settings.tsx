import { type FormEvent, useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/hooks/use-auth";
import { useTags, useCreateTag, useUpdateTag, useDeleteTag } from "@/hooks/use-tags";
import { getMySystem, updateMySystem, exportData } from "@/lib/systems";
import { PageHeader } from "@/components/page-header";
import { ColorDot } from "@/components/color-dot";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { PrivacyLevel } from "@/types/api";

function SystemSettings() {
  const qc = useQueryClient();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const update = useMutation({
    mutationFn: updateMySystem,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["system", "me"] }),
  });

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tag, setTag] = useState("");
  const [color, setColor] = useState("");
  const [privacy, setPrivacy] = useState<PrivacyLevel>("private");

  useEffect(() => {
    if (system) {
      setName(system.name);
      setDescription(system.description ?? "");
      setTag(system.tag ?? "");
      setColor(system.color ?? "");
      setPrivacy(system.privacy);
    }
  }, [system]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    update.mutate({
      name,
      description: description || null,
      tag: tag || null,
      color: color || null,
      privacy,
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">System profile</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label>Name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="space-y-2">
            <Label>Description</Label>
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Tag</Label>
              <Input
                value={tag}
                onChange={(e) => setTag(e.target.value)}
                placeholder="Short ID"
                maxLength={8}
              />
            </div>
            <div className="space-y-2">
              <Label>Color</Label>
              <div className="flex items-center gap-2">
                <Input
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
            <Label>Privacy</Label>
            <Select value={privacy} onValueChange={(v) => setPrivacy(v as PrivacyLevel)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="private">Private</SelectItem>
                <SelectItem value="friends">Friends only</SelectItem>
                <SelectItem value="public">Public</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button type="submit" disabled={update.isPending}>
            {update.isPending ? "Saving..." : "Save"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function TagsManager() {
  const { data: tags } = useTags();
  const createTag = useCreateTag();
  const updateTag = useUpdateTag();
  const deleteTag = useDeleteTag();
  const [newName, setNewName] = useState("");
  const [newColor, setNewColor] = useState("#10b981");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editColor, setEditColor] = useState("");

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!newName) return;
    createTag.mutate(
      { name: newName, color: newColor || null },
      { onSuccess: () => { setNewName(""); setNewColor("#10b981"); } },
    );
  }

  function startEdit(tag: { id: string; name: string; color: string | null }) {
    setEditingId(tag.id);
    setEditName(tag.name);
    setEditColor(tag.color ?? "#10b981");
  }

  function handleUpdate(e: FormEvent) {
    e.preventDefault();
    if (!editingId) return;
    updateTag.mutate(
      { id: editingId, data: { name: editName, color: editColor || null } },
      { onSuccess: () => setEditingId(null) },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Tags</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={handleCreate} className="flex items-end gap-2">
          <div className="flex-1 space-y-1">
            <Label className="text-xs">New tag</Label>
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Tag name"
            />
          </div>
          <Input
            type="color"
            value={newColor}
            onChange={(e) => setNewColor(e.target.value)}
            className="h-10 w-14 p-1"
          />
          <Button type="submit" size="sm" disabled={createTag.isPending || !newName}>
            Add
          </Button>
        </form>

        <div className="flex flex-wrap gap-2">
          {tags?.map((t) =>
            editingId === t.id ? (
              <form
                key={t.id}
                onSubmit={handleUpdate}
                className="flex items-center gap-1"
              >
                <Input
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="h-7 w-24 text-xs"
                />
                <Input
                  type="color"
                  value={editColor}
                  onChange={(e) => setEditColor(e.target.value)}
                  className="h-7 w-10 p-0.5"
                />
                <Button type="submit" size="sm" variant="ghost" className="h-7 px-2 text-xs">
                  Save
                </Button>
              </form>
            ) : (
              <Badge
                key={t.id}
                variant="outline"
                className="cursor-pointer gap-1.5"
                onClick={() => startEdit(t)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  deleteTag.mutate(t.id);
                }}
              >
                <ColorDot color={t.color} />
                {t.name}
              </Badge>
            ),
          )}
        </div>
        {tags && tags.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Click to edit. Right-click to delete.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function DataExport() {
  const [exporting, setExporting] = useState(false);

  async function handleExport() {
    setExporting(true);
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
    } finally {
      setExporting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Data export</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground mb-3">
          Download all your data as a JSON file.
        </p>
        <Button onClick={handleExport} variant="outline" disabled={exporting}>
          {exporting ? "Exporting..." : "Export data"}
        </Button>
      </CardContent>
    </Card>
  );
}

function AccountInfo() {
  const { user } = useAuth();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Account</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <div>
          <span className="text-muted-foreground">Email:</span> {user?.email}
        </div>
        <div>
          <span className="text-muted-foreground">2FA:</span>{" "}
          {user?.totp_enabled ? "Enabled" : "Disabled"}
        </div>
        <div>
          <span className="text-muted-foreground">Tier:</span>{" "}
          <Badge variant="outline">{user?.tier}</Badge>
        </div>
      </CardContent>
    </Card>
  );
}

export function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" />
      <div className="grid gap-6 max-w-2xl">
        <SystemSettings />
        <TagsManager />
        <Separator />
        <AccountInfo />
        <DataExport />
      </div>
    </>
  );
}
