import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/hooks/use-auth";
import { useTags, useCreateTag, useUpdateTag, useDeleteTag } from "@/hooks/use-tags";
import { useCustomFields, useCreateField, useUpdateField, useDeleteField } from "@/hooks/use-custom-fields";
import { getMySystem, updateMySystem, updateDeleteConfirmation, exportData } from "@/lib/systems";
import { AvatarUpload } from "@/components/avatar-upload";
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
import { dateFormatLabels } from "@/lib/date-format";
import { useShowImageBadges } from "@/hooks/use-preferences";
import { TOTPSetup } from "@/components/totp-setup";
import type { DateFormat, DeleteConfirmation, FieldType, PrivacyLevel } from "@/types/api";

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
  initial: { name: string; description: string | null; tag: string | null; avatar_url: string | null; color: string | null; privacy: PrivacyLevel; date_format?: DateFormat };
  onSubmit: (data: { name: string; description: string | null; tag: string | null; avatar_url: string | null; color: string | null; privacy: PrivacyLevel; date_format: DateFormat }) => void;
  loading: boolean;
}) {
  const [name, setName] = useState(initial.name);
  const [avatarUrl, setAvatarUrl] = useState(initial.avatar_url);
  const [description, setDescription] = useState(initial.description ?? "");
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
          <div className="space-y-2">
            <Label>Date format</Label>
            <Select value={dateFormat} onValueChange={(v) => setDateFormat(v as DateFormat)}>
              <SelectTrigger>
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

function CustomFieldsManager() {
  const { data: fields } = useCustomFields();
  const createField = useCreateField();
  const updateField = useUpdateField();
  const deleteField = useDeleteField();
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState<FieldType>("text");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!newName) return;
    createField.mutate(
      { name: newName, field_type: newType },
      { onSuccess: () => { setNewName(""); setNewType("text"); } },
    );
  }

  function startEdit(field: { id: string; name: string }) {
    setEditingId(field.id);
    setEditName(field.name);
  }

  function handleUpdate(e: FormEvent) {
    e.preventDefault();
    if (!editingId) return;
    updateField.mutate(
      { id: editingId, data: { name: editName } },
      { onSuccess: () => setEditingId(null) },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Custom fields</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={handleCreate} className="flex items-end gap-2">
          <div className="flex-1 space-y-1">
            <Label className="text-xs">Field name</Label>
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="e.g. Species, Role"
            />
          </div>
          <Select value={newType} onValueChange={(v) => setNewType(v as FieldType)}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="text">Text</SelectItem>
              <SelectItem value="number">Number</SelectItem>
              <SelectItem value="date">Date</SelectItem>
              <SelectItem value="boolean">Yes/No</SelectItem>
            </SelectContent>
          </Select>
          <Button type="submit" size="sm" disabled={createField.isPending || !newName}>
            Add
          </Button>
        </form>

        <div className="space-y-2">
          {fields?.map((f) =>
            editingId === f.id ? (
              <form
                key={f.id}
                onSubmit={handleUpdate}
                className="flex items-center gap-2"
              >
                <Input
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="h-8 flex-1 text-sm"
                />
                <Button type="submit" size="sm" variant="ghost" className="h-8 px-2 text-xs">
                  Save
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-8 px-2 text-xs"
                  onClick={() => setEditingId(null)}
                >
                  Cancel
                </Button>
              </form>
            ) : (
              <div
                key={f.id}
                className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
              >
                <span className="cursor-pointer" onClick={() => startEdit(f)}>
                  {f.name}
                  <span className="ml-2 text-xs text-muted-foreground">{f.field_type}</span>
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs text-destructive hover:text-destructive"
                  onClick={() => deleteField.mutate(f.id)}
                >
                  Delete
                </Button>
              </div>
            ),
          )}
        </div>
        {fields && fields.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Click a field name to rename it. Values are set per-member in the member editor.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function DeleteConfirmationSetting() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const mutation = useMutation({
    mutationFn: updateDeleteConfirmation,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["system", "me"] }),
  });

  const [pending, setPending] = useState<DeleteConfirmation | null>(null);
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState("");

  if (!system) return null;

  function handleChange(value: DeleteConfirmation) {
    if (value === system!.delete_confirmation) return;
    setPending(value);
    setPassword("");
    setTotpCode("");
    setError("");
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!pending) return;
    setError("");
    mutation.mutate(
      { level: pending, password, totp_code: totpCode || undefined },
      {
        onSuccess: () => setPending(null),
        onError: (err) => setError(err instanceof Error ? err.message : "Failed"),
      },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Delete confirmation</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Require extra verification before deleting a member.
        </p>
        <Select
          value={pending ?? system.delete_confirmation}
          onValueChange={(v) => handleChange(v as DeleteConfirmation)}
        >
          <SelectTrigger className="w-48">
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

        {pending && (
          <form onSubmit={handleSubmit} className="space-y-3 border-t pt-3">
            <p className="text-sm text-muted-foreground">
              Confirm your identity to change this setting.
            </p>
            <div className="space-y-1">
              <Label className="text-sm">Password</Label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            {user?.totp_enabled && (
              <div className="space-y-1">
                <Label className="text-sm">TOTP code</Label>
                <Input
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                  placeholder="6-digit code"
                  maxLength={6}
                  required
                />
              </div>
            )}
            {error && <p className="text-sm text-destructive">{error}</p>}
            <div className="flex gap-2">
              <Button type="submit" size="sm" disabled={mutation.isPending}>
                {mutation.isPending ? "Saving..." : "Confirm"}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setPending(null)}
              >
                Cancel
              </Button>
            </div>
          </form>
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
      <CardContent className="space-y-4 text-sm">
        <div className="space-y-2">
          <div>
            <span className="text-muted-foreground">Email:</span> {user?.email}
          </div>
          <div>
            <span className="text-muted-foreground">Tier:</span>{" "}
            <Badge variant="outline">{user?.tier}</Badge>
          </div>
        </div>
        <Separator />
        <div className="space-y-2">
          <p className="text-sm font-medium">Two-factor authentication</p>
          <TOTPSetup />
        </div>
      </CardContent>
    </Card>
  );
}

function DisplayPreferences() {
  const [showBadges, setShowBadges] = useShowImageBadges();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Display</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">Image source badges</p>
            <p className="text-xs text-muted-foreground">
              Show hosted/external labels on images in bios
            </p>
          </div>
          <Button
            variant={showBadges ? "default" : "outline"}
            size="sm"
            onClick={() => setShowBadges(!showBadges)}
          >
            {showBadges ? "On" : "Off"}
          </Button>
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
        <CustomFieldsManager />
        <DeleteConfirmationSetting />
        <DisplayPreferences />
        <Separator />
        <AccountInfo />
        <DataExport />
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Import data</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground mb-3">
              Import your data from SimplyPlural or other sources.
            </p>
            <Link to="/import">
              <Button variant="outline">Import from SimplyPlural</Button>
            </Link>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
