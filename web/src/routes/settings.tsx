import { type FormEvent, useState, useCallback } from "react";
import { Link } from "react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/hooks/use-auth";
import { useTags, useCreateTag, useUpdateTag, useDeleteTag } from "@/hooks/use-tags";
import { useCustomFields, useCreateField, useUpdateField, useDeleteField } from "@/hooks/use-custom-fields";
import { getMySystem, updateMySystem, updateDeleteConfirmation, exportData } from "@/lib/systems";
import { getStorageUsage, cleanupFiles, listFiles, deleteFile, type UploadedFileInfo } from "@/lib/files";
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
import { Checkbox } from "@/components/ui/checkbox";
import { useShowImageBadges } from "@/hooks/use-preferences";
import { useUiScale } from "@/hooks/use-theme";
import { TOTPSetup } from "@/components/totp-setup";
import { ChangePassword } from "@/components/change-password";
import { ChangeEmail } from "@/components/change-email";
import type { ApiKey, ApiKeyCreated, DateFormat, DeleteConfirmation, FieldType, PrivacyLevel } from "@/types/api";
import { listApiKeys, createApiKey, revokeApiKey } from "@/lib/api-keys";
import { getSessions, renameSession, revokeSession, revokeOtherSessions, requestAccountDeletion, cancelDeletion, updateMe, getAuthConfig, getTrustedDevices, renameTrustedDevice, revokeTrustedDevice, revokeAllTrustedDevices, type Session, type TrustedDevice } from "@/lib/auth";
import { listClientSettings, deleteClientSettings } from "@/lib/client-settings";
import { timeAgo } from "@/lib/utils";
import { Pencil, AlertTriangle } from "lucide-react";
import { ApiError } from "@/lib/api-client";
import { toast } from "sonner";

function SystemSettings() {
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
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);

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
                {deleteConfirmId === f.id ? (
                  <div className="flex items-center gap-1">
                    <Button
                      variant="destructive"
                      size="sm"
                      className="h-6 px-2 text-xs"
                      onClick={() => {
                        deleteField.mutate(f.id);
                        setDeleteConfirmId(null);
                      }}
                    >
                      Confirm
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-6 px-2 text-xs"
                      onClick={() => setDeleteConfirmId(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                ) : (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-xs text-destructive hover:text-destructive"
                    onClick={() => setDeleteConfirmId(f.id)}
                  >
                    Delete
                  </Button>
                )}
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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "me"] });
      toast.success("Delete confirmation updated");
    },
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
                  autoComplete="off"
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
      toast.success("Data exported");
    } catch {
      toast.error("Export failed");
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
  const { user, refreshUser } = useAuth();
  const newsletterToggle = useMutation({
    mutationFn: (newsletter_opt_in: boolean) => updateMe({ newsletter_opt_in }),
    onSuccess: async () => {
      await refreshUser();
      toast.success("Preferences saved");
    },
    onError: () => toast.error("Failed to save preferences"),
  });

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
          <p className="text-sm font-medium">Email</p>
          <ChangeEmail />
        </div>
        <Separator />
        <div className="space-y-2">
          <p className="text-sm font-medium">Password</p>
          <ChangePassword />
        </div>
        <Separator />
        <div className="space-y-2">
          <p className="text-sm font-medium">Two-factor authentication</p>
          <TOTPSetup />
        </div>
        <Separator />
        <div className="flex items-start gap-3">
          <Checkbox
            id="newsletter-opt-in"
            checked={user?.newsletter_opt_in ?? false}
            onCheckedChange={(v) => newsletterToggle.mutate(v === true)}
            disabled={newsletterToggle.isPending}
          />
          <div>
            <Label
              htmlFor="newsletter-opt-in"
              className="text-sm font-medium cursor-pointer"
            >
              Product updates email
            </Label>
            <p className="text-xs text-muted-foreground mt-0.5">
              Occasional updates about Sheaf — new features and important
              changes. Transactional mail (password reset, security alerts,
              etc.) is not affected by this setting.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

function StorageUsageCard() {
  const qc = useQueryClient();
  const { data: usage } = useQuery({
    queryKey: ["storage", "usage"],
    queryFn: getStorageUsage,
  });
  const cleanup = useMutation({
    mutationFn: cleanupFiles,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["storage", "usage"] });
      if (data?.orphaned > 0) {
        toast.success(`Cleaned up ${data.orphaned} orphaned file(s)`);
      } else {
        toast.success("No orphaned files found");
      }
    },
  });

  if (!usage) return null;

  const unlimited = usage.quota_bytes === 0;
  const percent = unlimited ? 0 : Math.round((usage.used_bytes / usage.quota_bytes) * 100);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Storage</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1">
          <div className="flex justify-between text-sm">
            <span>{formatBytes(usage.used_bytes)} used</span>
            <span className="text-muted-foreground">
              {unlimited ? "Unlimited" : formatBytes(usage.quota_bytes)}
            </span>
          </div>
          {!unlimited && (
            <div className="h-2 rounded-full bg-muted overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  percent > 90 ? "bg-red-500" : percent > 70 ? "bg-yellow-500" : "bg-green-500"
                }`}
                style={{ width: `${Math.min(percent, 100)}%` }}
              />
            </div>
          )}
        </div>
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            Remove unused uploaded images to free space
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => cleanup.mutate()}
            disabled={cleanup.isPending}
          >
            {cleanup.isPending ? "Cleaning..." : "Clean up"}
          </Button>
        </div>
        {cleanup.data && cleanup.data.orphaned > 0 && (
          <p className="text-xs text-green-600">
            Removed {cleanup.data.orphaned} file{cleanup.data.orphaned !== 1 ? "s" : ""}, freed {formatBytes(cleanup.data.freed_bytes)}
          </p>
        )}
        {cleanup.data && cleanup.data.orphaned === 0 && (
          <p className="text-xs text-muted-foreground">No orphaned files found</p>
        )}
      </CardContent>
    </Card>
  );
}

function UploadedFilesCard() {
  const qc = useQueryClient();
  const { data: files, isLoading } = useQuery({
    queryKey: ["files", "list"],
    queryFn: listFiles,
  });
  const remove = useMutation({
    mutationFn: deleteFile,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["files", "list"] });
      qc.invalidateQueries({ queryKey: ["storage", "usage"] });
      toast.success("File deleted");
    },
  });
  const [confirmId, setConfirmId] = useState<string | null>(null);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Uploaded files</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading...</p>
        )}
        {files && files.length === 0 && (
          <p className="text-sm text-muted-foreground">No uploaded files.</p>
        )}
        {files && files.length > 0 && (
          <div className="grid grid-cols-4 sm:grid-cols-6 gap-2">
            {files.map((f: UploadedFileInfo) => (
              <div
                key={f.id}
                className="group relative aspect-square rounded-md border overflow-hidden"
              >
                <img
                  src={f.url}
                  alt=""
                  className="h-full w-full object-cover"
                />
                <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-colors" />
                <div className="absolute top-0 right-0 opacity-0 group-hover:opacity-100 transition-opacity p-0.5">
                  {confirmId === f.id ? (
                    <Button
                      variant="destructive"
                      size="sm"
                      className="h-5 text-[10px] px-1"
                      onClick={() => {
                        remove.mutate(f.id);
                        setConfirmId(null);
                      }}
                      disabled={remove.isPending}
                    >
                      Confirm
                    </Button>
                  ) : (
                    <Button
                      variant="destructive"
                      size="sm"
                      className="h-5 text-[10px] px-1"
                      onClick={() => setConfirmId(f.id)}
                    >
                      Delete
                    </Button>
                  )}
                </div>
                <span className="absolute bottom-0 left-0 right-0 bg-black/60 text-white text-[10px] px-1 py-0.5 truncate">
                  {f.purpose} · {formatBytes(f.size_bytes)}
                </span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DisplayPreferences() {
  const [showBadges, setShowBadges] = useShowImageBadges();
  const { scale, setScale, scales } = useUiScale();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Display</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">UI scale</p>
            <p className="text-xs text-muted-foreground">
              Adjust the interface size
            </p>
          </div>
          <div className="flex gap-1">
            {scales.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setScale(s)}
                className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
                  scale === s
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground hover:bg-muted/80"
                }`}
              >
                {s}%
              </button>
            ))}
          </div>
        </div>
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

function FrontPreferences() {
  const qc = useQueryClient();
  const { data: system } = useQuery({ queryKey: ["system", "me"], queryFn: getMySystem });
  const update = useMutation({
    mutationFn: (replace_fronts_default: boolean) => updateMySystem({ replace_fronts_default }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "me"] });
      toast.success("Front preferences saved");
    },
  });

  if (!system) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Fronting</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-start gap-3">
          <Checkbox
            id="replace-fronts-default"
            checked={system.replace_fronts_default}
            onCheckedChange={(v) => update.mutate(v === true)}
            disabled={update.isPending}
          />
          <div>
            <Label htmlFor="replace-fronts-default" className="text-sm font-medium cursor-pointer">
              End current fronts when starting a new one
            </Label>
            <p className="text-xs text-muted-foreground mt-0.5">
              This is the default for the "End all current fronts" checkbox in the start front dialog.
              You can override it per front.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

interface ScopeResource {
  key: string;
  label: string;
  hasDelete?: boolean;
  readOnly?: boolean;
}

const SCOPE_RESOURCES: ScopeResource[] = [
  { key: "system", label: "System" },
  { key: "members", label: "Members", hasDelete: true },
  { key: "fronts", label: "Fronts", hasDelete: true },
  { key: "groups", label: "Groups", hasDelete: true },
  { key: "tags", label: "Tags", hasDelete: true },
  { key: "fields", label: "Custom fields", hasDelete: true },
  { key: "export", label: "Data export", readOnly: true },
];

type ScopeLevel = "none" | "read" | "write" | "write+delete";

function scopesFromLevels(levels: Record<string, ScopeLevel>, isAdmin: boolean, adminLevel: ScopeLevel): string[] {
  const scopes: string[] = [];
  for (const { key, readOnly, hasDelete } of SCOPE_RESOURCES) {
    const level = levels[key] ?? "none";
    if (level === "none") continue;
    if (readOnly) {
      scopes.push(`${key}:read`);
    } else if (level === "read") {
      scopes.push(`${key}:read`);
    } else if (level === "write") {
      scopes.push(`${key}:write`);
    } else if (level === "write+delete" && hasDelete) {
      scopes.push(`${key}:write`, `${key}:delete`);
    }
  }
  if (isAdmin && adminLevel !== "none") {
    scopes.push(adminLevel === "write" ? "admin:write" : "admin:read");
  }
  return scopes;
}

function ScopeRow({
  label,
  value,
  onChange,
  readOnly,
  hasDelete,
}: {
  label: string;
  value: ScopeLevel;
  onChange: (v: ScopeLevel) => void;
  readOnly?: boolean;
  hasDelete?: boolean;
}) {
  const options: { v: ScopeLevel; label: string }[] = [
    { v: "none", label: "None" },
    { v: "read", label: "Read" },
    ...(!readOnly ? [{ v: "write" as ScopeLevel, label: "Read+Write" }] : []),
    ...(hasDelete ? [{ v: "write+delete" as ScopeLevel, label: "Write+Delete" }] : []),
  ];
  return (
    <div className="flex items-center justify-between py-1.5 text-sm">
      <span className="w-32 text-muted-foreground">{label}</span>
      <div className="flex gap-1">
        {options.map((o) => (
          <button
            key={o.v}
            type="button"
            onClick={() => onChange(o.v)}
            className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
              value === o.v
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:bg-muted/80"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function ApiKeysCard() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const { data: keys } = useQuery({ queryKey: ["api-keys"], queryFn: listApiKeys });
  const revoke = useMutation({
    mutationFn: revokeApiKey,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["api-keys"] });
      toast.success("API key revoked");
    },
  });
  const create = useMutation({
    mutationFn: createApiKey,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys"] }),
  });

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [levels, setLevels] = useState<Record<string, ScopeLevel>>({});
  const [adminLevel, setAdminLevel] = useState<ScopeLevel>("none");
  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [revokeConfirmId, setRevokeConfirmId] = useState<string | null>(null);

  const setLevel = useCallback((key: string, v: ScopeLevel) => {
    setLevels((prev) => ({ ...prev, [key]: v }));
  }, []);

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    const scopes = scopesFromLevels(levels, !!user?.is_admin, adminLevel);
    create.mutate(
      { name, scopes },
      {
        onSuccess: (k) => {
          setCreatedKey(k);
          setShowForm(false);
          setName("");
          setLevels({});
          setAdminLevel("none");
        },
      },
    );
  }

  function handleCopy() {
    if (!createdKey) return;
    navigator.clipboard.writeText(createdKey.key);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">API keys</CardTitle>
        {!showForm && !createdKey && (
          <Button size="sm" variant="outline" onClick={() => setShowForm(true)}>
            New key
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {createdKey && (
          <div className="rounded-md border border-yellow-500/30 bg-yellow-500/5 p-3 space-y-2">
            <p className="text-sm font-medium text-yellow-700 dark:text-yellow-400">
              Copy this key now — it won't be shown again.
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 rounded bg-muted px-2 py-1.5 text-xs font-mono break-all">
                {createdKey.key}
              </code>
              <Button size="sm" variant="outline" onClick={handleCopy}>
                {copied ? "Copied!" : "Copy"}
              </Button>
            </div>
            <Button size="sm" variant="ghost" onClick={() => setCreatedKey(null)}>
              Done
            </Button>
          </div>
        )}

        {showForm && (
          <form onSubmit={handleCreate} className="space-y-4 rounded-md border p-4">
            <div className="space-y-1">
              <Label className="text-sm">Key name</Label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Mobile app, Scripts"
                required
              />
            </div>
            <div className="space-y-1">
              <Label className="text-sm">Scopes</Label>
              <div className="divide-y rounded-md border px-3">
                {SCOPE_RESOURCES.map(({ key, label, readOnly, hasDelete }) => (
                  <ScopeRow
                    key={key}
                    label={label}
                    value={levels[key] ?? "none"}
                    onChange={(v) => setLevel(key, v)}
                    readOnly={readOnly}
                    hasDelete={hasDelete}
                  />
                ))}
                {user?.is_admin && (
                  <>
                    <div className="py-1.5 text-xs text-muted-foreground font-medium">Admin</div>
                    <ScopeRow
                      label="Admin"
                      value={adminLevel}
                      onChange={setAdminLevel}
                    />
                  </>
                )}
              </div>
            </div>
            <div className="flex gap-2">
              <Button type="submit" size="sm" disabled={create.isPending || !name}>
                {create.isPending ? "Creating..." : "Create key"}
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setShowForm(false)}>
                Cancel
              </Button>
            </div>
          </form>
        )}

        {keys && keys.length > 0 ? (
          <div className="space-y-2">
            {keys.map((k: ApiKey) => (
              <div
                key={k.id}
                className="flex items-start justify-between rounded-md border px-3 py-2 text-sm"
              >
                <div className="space-y-1">
                  <p className="font-medium">{k.name}</p>
                  <div className="flex flex-wrap gap-1">
                    {k.scopes.map((s) => (
                      <Badge key={s} variant="outline" className="text-xs">
                        {s}
                      </Badge>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Created {new Date(k.created_at).toLocaleDateString()}
                    {k.last_used_at && ` · Last used ${new Date(k.last_used_at).toLocaleDateString()}`}
                    {k.expires_at && ` · Expires ${new Date(k.expires_at).toLocaleDateString()}`}
                  </p>
                </div>
                {revokeConfirmId === k.id ? (
                  <div className="flex items-center gap-1 shrink-0">
                    <Button
                      variant="destructive"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => {
                        revoke.mutate(k.id);
                        setRevokeConfirmId(null);
                      }}
                      disabled={revoke.isPending}
                    >
                      Confirm
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => setRevokeConfirmId(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                ) : (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-destructive hover:text-destructive shrink-0"
                    onClick={() => setRevokeConfirmId(k.id)}
                    disabled={revoke.isPending}
                  >
                    Revoke
                  </Button>
                )}
              </div>
            ))}
          </div>
        ) : (
          !showForm && !createdKey && (
            <p className="text-sm text-muted-foreground">No API keys yet.</p>
          )
        )}
      </CardContent>
    </Card>
  );
}

function ActiveSessionsCard() {
  const qc = useQueryClient();
  const { data: sessions } = useQuery({
    queryKey: ["sessions"],
    queryFn: getSessions,
  });
  const revoke = useMutation({
    mutationFn: revokeSession,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
      toast.success("Session revoked");
    },
  });
  const revokeAll = useMutation({
    mutationFn: revokeOtherSessions,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
      toast.success("Other sessions revoked");
    },
  });
  const renameMut = useMutation({
    mutationFn: ({ id, nickname }: { id: string; nickname: string }) =>
      renameSession(id, nickname),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
      toast.success("Session renamed");
    },
  });

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editNickname, setEditNickname] = useState("");

  function startEdit(s: Session) {
    setEditingId(s.id);
    setEditNickname(s.nickname ?? "");
  }

  function saveNickname() {
    if (editingId) {
      renameMut.mutate({ id: editingId, nickname: editNickname });
      setEditingId(null);
    }
  }

  const otherCount = sessions?.filter((s) => !s.is_current).length ?? 0;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">Active sessions</CardTitle>
        {otherCount > 0 && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => revokeAll.mutate()}
            disabled={revokeAll.isPending}
          >
            {revokeAll.isPending ? "Revoking..." : "Revoke all others"}
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {revokeAll.data && revokeAll.data.revoked > 0 && (
          <p className="text-xs text-green-600">
            Revoked {revokeAll.data.revoked} session{revokeAll.data.revoked !== 1 ? "s" : ""}
          </p>
        )}
        {sessions && sessions.length === 0 && (
          <p className="text-sm text-muted-foreground">No active sessions.</p>
        )}
        {sessions?.map((s) => (
          <div
            key={s.id}
            className="flex items-start justify-between rounded-md border px-3 py-2 text-sm"
          >
            <div className="space-y-1 min-w-0 flex-1">
              <div className="flex items-center gap-2">
                {editingId === s.id ? (
                  <form
                    className="flex items-center gap-1"
                    onSubmit={(e) => {
                      e.preventDefault();
                      saveNickname();
                    }}
                  >
                    <Input
                      value={editNickname}
                      onChange={(e) => setEditNickname(e.target.value)}
                      className="h-6 w-40 text-xs"
                      placeholder="Session name"
                      autoFocus
                      onBlur={saveNickname}
                    />
                  </form>
                ) : (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 font-medium hover:text-muted-foreground transition-colors"
                    onClick={() => startEdit(s)}
                  >
                    {s.nickname || s.client_name}
                    <Pencil className="h-3 w-3 text-muted-foreground" />
                  </button>
                )}
                {s.is_current && (
                  <Badge variant="outline" className="text-xs">
                    Current
                  </Badge>
                )}
              </div>
              {s.nickname && editingId !== s.id && (
                <p className="text-xs text-muted-foreground">{s.client_name}</p>
              )}
              <p className="text-xs text-muted-foreground">
                Last active {timeAgo(s.last_active_at)}
                {s.last_active_ip && ` from ${s.last_active_ip}`}
                {" · "}Created {new Date(s.created_at).toLocaleDateString()}
                {s.created_ip && ` from ${s.created_ip}`}
              </p>
            </div>
            {!s.is_current && (
              <Button
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive shrink-0"
                onClick={() => revoke.mutate(s.id)}
                disabled={revoke.isPending}
              >
                Revoke
              </Button>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function TrustedDevicesCard() {
  const qc = useQueryClient();
  const { data: devices } = useQuery({
    queryKey: ["trusted-devices"],
    queryFn: getTrustedDevices,
  });
  const revoke = useMutation({
    mutationFn: revokeTrustedDevice,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trusted-devices"] });
      toast.success("Device revoked");
    },
  });
  const revokeAll = useMutation({
    mutationFn: revokeAllTrustedDevices,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trusted-devices"] });
      toast.success("All trusted devices revoked");
    },
  });
  const renameMut = useMutation({
    mutationFn: ({ id, nickname }: { id: string; nickname: string }) =>
      renameTrustedDevice(id, nickname),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trusted-devices"] });
      toast.success("Device renamed");
    },
  });

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editNickname, setEditNickname] = useState("");

  function startEdit(d: TrustedDevice) {
    setEditingId(d.id);
    setEditNickname(d.nickname ?? "");
  }

  function saveNickname() {
    if (editingId) {
      renameMut.mutate({ id: editingId, nickname: editNickname });
      setEditingId(null);
    }
  }

  if (!devices) return null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">Trusted devices</CardTitle>
        {devices.length > 0 && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => revokeAll.mutate()}
            disabled={revokeAll.isPending}
          >
            {revokeAll.isPending ? "Revoking..." : "Revoke all"}
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Browsers that can skip the 2FA prompt for 30 days. Revoked
          automatically when you change your password or disable 2FA.
        </p>
        {devices.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No trusted devices.
          </p>
        )}
        {devices.map((d) => (
          <div
            key={d.id}
            className="flex items-start justify-between rounded-md border px-3 py-2 text-sm"
          >
            <div className="space-y-1 min-w-0 flex-1">
              <div className="flex items-center gap-2">
                {editingId === d.id ? (
                  <form
                    className="flex items-center gap-1"
                    onSubmit={(e) => {
                      e.preventDefault();
                      saveNickname();
                    }}
                  >
                    <Input
                      value={editNickname}
                      onChange={(e) => setEditNickname(e.target.value)}
                      className="h-6 w-40 text-xs"
                      placeholder="Device name"
                      autoFocus
                      onBlur={saveNickname}
                    />
                  </form>
                ) : (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 font-medium hover:text-muted-foreground transition-colors"
                    onClick={() => startEdit(d)}
                  >
                    {d.nickname || d.user_agent.slice(0, 60) || "Device"}
                    <Pencil className="h-3 w-3 text-muted-foreground" />
                  </button>
                )}
                {d.is_current && (
                  <Badge variant="outline" className="text-xs">
                    This browser
                  </Badge>
                )}
              </div>
              {d.nickname && editingId !== d.id && (
                <p className="text-xs text-muted-foreground truncate">
                  {d.user_agent}
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                {d.last_used_at
                  ? `Last used ${timeAgo(d.last_used_at)}`
                  : "Not yet used"}
                {d.last_used_ip && ` from ${d.last_used_ip}`}
                {" · "}Expires {new Date(d.expires_at).toLocaleDateString()}
              </p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive shrink-0"
              onClick={() => revoke.mutate(d.id)}
              disabled={revoke.isPending}
            >
              Revoke
            </Button>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function DeleteAccountCard() {
  const { user, refreshUser } = useAuth();
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  const { data: config } = useQuery({ queryKey: ["auth-config"], queryFn: getAuthConfig });
  const isPending = user?.account_status === "pending_deletion";
  const graceDays = config?.account_deletion_grace_days ?? 7;

  async function handleDelete(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await requestAccountDeletion(password, totpCode || undefined);
      await refreshUser();
      setPassword("");
      setTotpCode("");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        setError("Something went wrong");
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCancel() {
    setCancelling(true);
    try {
      await cancelDeletion();
      await refreshUser();
    } catch {
      // Error toast handled by apiFetch
    } finally {
      setCancelling(false);
    }
  }

  if (isPending) {
    const deletionDate = user?.deletion_scheduled_for
      ? new Date(user.deletion_scheduled_for)
      : null;

    return (
      <Card className="border-destructive/50">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-4 w-4" />
            Account deletion scheduled
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Your account is scheduled for permanent deletion
            {deletionDate
              ? ` on ${deletionDate.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })}`
              : ""}.
            All your data will be permanently removed.
          </p>
          <Button
            variant="outline"
            onClick={handleCancel}
            disabled={cancelling}
          >
            {cancelling ? "Cancelling..." : "Cancel deletion"}
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-destructive/50">
      <CardHeader>
        <CardTitle className="text-base text-destructive">
          Delete account
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground mb-4">
          Permanently delete your account and all associated data. You will have
          a {graceDays}-day grace period to change your mind.
        </p>
        <form onSubmit={handleDelete} className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="delete-password">Confirm your password</Label>
            <Input
              id="delete-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
            />
          </div>
          {user?.totp_enabled && (
            <div className="space-y-2">
              <Label htmlFor="delete-totp">2FA code</Label>
              <Input
                id="delete-totp"
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                placeholder="Enter TOTP or recovery code"
                autoComplete="one-time-code"
              />
            </div>
          )}
          {error && (
            <p className="text-sm text-destructive-foreground">{error}</p>
          )}
          <Button
            type="submit"
            variant="destructive"
            disabled={submitting || !password}
          >
            {submitting ? "Requesting deletion..." : "Delete my account"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function ClientSettingsCard() {
  const qc = useQueryClient();
  const { data: entries, isLoading } = useQuery({
    queryKey: ["client-settings"],
    queryFn: listClientSettings,
  });

  const deleteMut = useMutation({
    mutationFn: deleteClientSettings,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["client-settings"] });
      toast.success("Client settings deleted");
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Client Settings</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground mb-3">
          Settings stored by apps and integrations that use this account.
        </p>
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading...</p>
        )}
        {entries && entries.length === 0 && (
          <p className="text-sm text-muted-foreground">No client settings stored.</p>
        )}
        {entries && entries.length > 0 && (
          <div className="space-y-2">
            {entries.map((entry) => (
              <div
                key={entry.client_id}
                className="flex items-center justify-between rounded-md border px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium">{entry.client_id}</p>
                  <p className="text-xs text-muted-foreground">
                    {JSON.stringify(entry.settings).length.toLocaleString()} bytes
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:text-destructive shrink-0"
                  onClick={() => deleteMut.mutate(entry.client_id)}
                  disabled={deleteMut.isPending}
                >
                  Delete
                </Button>
              </div>
            ))}
          </div>
        )}
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
        <FrontPreferences />
        <Separator />
        <AccountInfo />
        <ApiKeysCard />
        <ActiveSessionsCard />
        <TrustedDevicesCard />
        <StorageUsageCard />
        <UploadedFilesCard />
        <ClientSettingsCard />
        <DataExport />
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Import data</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground mb-3">
              Supported formats: SimplyPlural, Sheaf
            </p>
            <Link to="/import">
              <Button variant="outline">Import data</Button>
            </Link>
          </CardContent>
        </Card>
        <Separator />
        <DeleteAccountCard />
      </div>
    </>
  );
}
