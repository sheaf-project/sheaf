import { type FormEvent, lazy, Suspense, useMemo, useState } from "react";
import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { useMembers, useCreateMember, useDeleteMember, useUpdateMember } from "@/hooks/use-members";
import { useCustomFields, useMemberFieldValues, useSetMemberFieldValues } from "@/hooks/use-custom-fields";
import { getMySystem } from "@/lib/systems";
import {
  getMemberTags,
  listMemberBioRevisions,
  pinMemberBioRevision,
  restoreMemberBioRevision,
  setMemberTags,
  unpinMemberBioRevision,
} from "@/lib/members";
import { listTags } from "@/lib/tags";
import { getNotifySettings, setNotifySettings } from "@/lib/messages";
import { getSystemSafety } from "@/lib/system-safety";
import { AvatarUpload } from "@/components/avatar-upload";
import { Badge } from "@/components/ui/badge";
import { ColorDot } from "@/components/color-dot";
import { ContentRevisionList } from "@/components/content-revision-list";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

const BioEditor = lazy(() => import("@/components/bio-editor").then(m => ({ default: m.BioEditor })));
const MarkdownPreview = lazy(() => import("@/components/bio-editor").then(m => ({ default: m.MarkdownPreview })));
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { DatePicker } from "@/components/date-picker";
import { PageHeader } from "@/components/page-header";
import { BookOpen, History, MessageSquare, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Member, MemberCreate, MemberUpdate, PrivacyLevel, DeleteConfirmation, CustomFieldValueSet } from "@/types/api";

function MemberForm({
  initial,
  onSubmit,
  loading,
  submitLabel,
}: {
  initial?: Partial<MemberCreate> & { privacy?: PrivacyLevel };
  onSubmit: (data: MemberCreate | MemberUpdate) => void;
  loading: boolean;
  submitLabel: string;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const [avatarUrl, setAvatarUrl] = useState(initial?.avatar_url ?? null);
  const [pronouns, setPronouns] = useState(initial?.pronouns ?? "");
  const [color, setColor] = useState(initial?.color ?? "#6366f1");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [note, setNote] = useState(initial?.note ?? "");
  const [birthday, setBirthday] = useState(initial?.birthday ?? "");
  const [pluralkitId, setPluralkitId] = useState(initial?.pluralkit_id ?? "");
  const [emoji, setEmoji] = useState(initial?.emoji ?? "");
  const [isCustomFront, setIsCustomFront] = useState(
    initial?.is_custom_front ?? false,
  );
  const [privacy, setPrivacy] = useState<PrivacyLevel>(initial?.privacy ?? "private");
  // Preserve an existing numeric pin priority when toggling stays on; a
  // freshly-pinned member gets priority 0.
  const initialPin = initial?.quick_switch_pin ?? null;
  const [pinned, setPinned] = useState(initialPin != null);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      name,
      display_name: displayName || null,
      avatar_url: avatarUrl,
      pronouns: pronouns || null,
      color: color || null,
      description: description || null,
      note: note || null,
      birthday: birthday || null,
      pluralkit_id: pluralkitId.trim() || null,
      emoji: emoji.trim() || null,
      is_custom_front: isCustomFront,
      privacy,
      quick_switch_pin: pinned ? (initialPin ?? 0) : null,
    });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <AvatarUpload
        url={avatarUrl}
        fallback={name.charAt(0).toUpperCase() || "?"}
        onUpload={setAvatarUrl}
        onRemove={() => setAvatarUrl(null)}
      />
      <div className="space-y-2">
        <Label htmlFor="member-name">Name</Label>
        <Input id="member-name" value={name} onChange={(e) => setName(e.target.value)} required />
      </div>
      <div className="grid grid-cols-[1fr_auto] gap-3">
        <div className="space-y-2">
          <Label htmlFor="member-display-name">Display name</Label>
          <Input
            id="member-display-name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Optional, shown instead of name if set"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="member-emoji">Emoji</Label>
          <Input
            id="member-emoji"
            value={emoji}
            onChange={(e) => setEmoji(e.target.value)}
            placeholder=""
            maxLength={8}
            className="w-20 text-center"
          />
        </div>
      </div>
      <div className="space-y-2">
        <Label htmlFor="member-pronouns">Pronouns</Label>
        <Input
          id="member-pronouns"
          value={pronouns}
          onChange={(e) => setPronouns(e.target.value)}
          placeholder="e.g. she/her"
        />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="member-color">Color</Label>
          <div className="flex items-center gap-2">
            <Input
              id="member-color"
              type="color"
              value={color}
              onChange={(e) => setColor(e.target.value)}
              className="h-10 w-14 p-1"
            />
            <Input
              value={color}
              onChange={(e) => setColor(e.target.value)}
              placeholder="#000000"
              className="flex-1"
            />
          </div>
        </div>
        <div className="space-y-2">
          <Label>Birthday</Label>
          <DatePicker
            value={birthday}
            onChange={setBirthday}
            placeholder="Birthday"
          />
        </div>
      </div>
      <div className="space-y-2">
        <Label>Bio</Label>
        <Suspense fallback={<div className="h-[120px] rounded-md border border-input" />}>
          <BioEditor value={description} onChange={setDescription} />
        </Suspense>
      </div>
      <div className="space-y-2">
        <Label htmlFor="member-note">Notes</Label>
        <textarea
          id="member-note"
          className="w-full rounded-md border bg-background p-2 text-sm font-mono"
          rows={4}
          maxLength={5000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Quick reference scratchpad — trigger list, fav drink, current med doses..."
        />
        <p className="text-xs text-muted-foreground">
          Markdown supported. Edits overwrite immediately. No revision
          history, not protected by System Safety.
        </p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="member-pluralkit-id">PluralKit ID</Label>
        <Input
          id="member-pluralkit-id"
          value={pluralkitId}
          onChange={(e) => setPluralkitId(e.target.value)}
          placeholder="Optional, e.g. wyyetr"
          maxLength={8}
          autoComplete="off"
          spellCheck={false}
        />
        <p className="text-xs text-muted-foreground">
          The 5-7 character HID PluralKit assigned this member, if you cross-reference between the two.
        </p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="member-privacy">Privacy</Label>
        <Select value={privacy} onValueChange={(v) => setPrivacy(v as PrivacyLevel)}>
          <SelectTrigger id="member-privacy">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="private">Private</SelectItem>
            <SelectItem value="friends">Friends only</SelectItem>
            <SelectItem value="public">Public</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <label className="flex items-start gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={isCustomFront}
          onChange={(e) => setIsCustomFront(e.target.checked)}
          className="h-4 w-4 mt-0.5 rounded border-input"
        />
        <div>
          <span className="text-sm font-medium">Custom front (not a member)</span>
          <p className="text-xs text-muted-foreground mt-0.5">
            Marks this entry as a fronting state like &quot;Asleep&quot; or &quot;Away&quot; rather
            than a system member. Custom fronts can still front and be in
            groups, but don&apos;t count toward member statistics and are
            listed separately from members.
          </p>
        </div>
      </label>
      <label className="flex items-start gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={pinned}
          onChange={(e) => setPinned(e.target.checked)}
          className="h-4 w-4 mt-0.5 rounded border-input"
        />
        <div>
          <span className="text-sm font-medium">Pin to quick-switch</span>
          <p className="text-xs text-muted-foreground mt-0.5">
            Keeps this member at the top of the start-front quick-pick
            list, ahead of the recently-fronted suggestions.
          </p>
        </div>
      </label>
      <DialogFooter>
        <Button type="submit" disabled={loading || !name}>
          {loading ? "Saving..." : submitLabel}
        </Button>
      </DialogFooter>
    </form>
  );
}

function MemberFieldValues({ memberId }: { memberId: string }) {
  const { data: fields } = useCustomFields();
  const { data: values } = useMemberFieldValues(memberId);
  const setValues = useSetMemberFieldValues();
  const [overrides, setOverrides] = useState<Record<string, string>>({});

  const serverValues = useMemo(() => {
    const map: Record<string, string> = {};
    if (values) {
      for (const v of values) {
        map[v.field_id] = typeof v.value === "object" && v.value !== null
          ? ((v.value as Record<string, unknown>).v as string ?? "")
          : String(v.value ?? "");
      }
    }
    return map;
  }, [values]);

  const dirty = Object.keys(overrides).length > 0;

  if (!fields || fields.length === 0) return null;

  function handleSave() {
    const merged = { ...serverValues, ...overrides };
    const payload: CustomFieldValueSet[] = fields!
      .filter((f) => merged[f.id] !== undefined && merged[f.id] !== "")
      .map((f) => ({ field_id: f.id, value: { v: merged[f.id] } }));
    setValues.mutate({ memberId, values: payload }, { onSuccess: () => setOverrides({}) });
  }

  return (
    <div className="space-y-3 border-t pt-3">
      <p className="text-sm font-medium text-muted-foreground">Custom fields</p>
      {fields.map((f) => (
        <div key={f.id} className="space-y-1">
          <Label htmlFor={`custom-field-${f.id}`} className="text-xs">{f.name}</Label>
          <Input
            id={`custom-field-${f.id}`}
            value={overrides[f.id] ?? serverValues[f.id] ?? ""}
            onChange={(e) => {
              setOverrides((prev) => ({ ...prev, [f.id]: e.target.value }));
            }}
            placeholder={f.field_type}
          />
        </div>
      ))}
      {dirty && (
        <Button
          size="sm"
          variant="outline"
          onClick={handleSave}
          disabled={setValues.isPending}
        >
          {setValues.isPending ? "Saving..." : "Save fields"}
        </Button>
      )}
    </div>
  );
}

function DeleteMemberDialog({
  member,
  level,
  onOpenChange,
  onDeleted,
}: {
  member: Member;
  level: DeleteConfirmation;
  onOpenChange: (open: boolean) => void;
  onDeleted: () => void;
}) {
  const deleteMember = useDeleteMember();
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState("");

  const needsPassword = level === "password" || level === "both";
  const needsTotp = level === "totp" || level === "both";

  function handleDelete() {
    setError("");
    const confirm: { password?: string; totp_code?: string } = {};
    if (needsPassword) confirm.password = password;
    if (needsTotp) confirm.totp_code = totpCode;

    deleteMember.mutate(
      { id: member.id, confirm: Object.keys(confirm).length > 0 ? confirm : undefined },
      {
        onSuccess: () => onDeleted(),
        onError: (err) => setError(err instanceof Error ? err.message : "Delete failed"),
      },
    );
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete member</DialogTitle>
          <DialogDescription>
            Are you sure you want to delete &quot;{member.name}&quot;? This cannot be undone.
          </DialogDescription>
        </DialogHeader>
        {(needsPassword || needsTotp) && (
          <div className="space-y-3">
            {needsPassword && (
              <div className="space-y-1">
                <Label htmlFor="member-delete-password" className="text-sm">Password</Label>
                <Input
                  id="member-delete-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                />
              </div>
            )}
            {needsTotp && (
              <div className="space-y-1">
                <Label htmlFor="member-delete-totp" className="text-sm">TOTP code</Label>
                <Input
                  id="member-delete-totp"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                  placeholder="6-digit code"
                  inputMode="numeric"
                  maxLength={6}
                  pattern="[0-9]{6}"
                  autoComplete="off"
                />
              </div>
            )}
          </div>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleDelete}
            disabled={
              deleteMember.isPending ||
              (needsPassword && !password) ||
              (needsTotp && !totpCode)
            }
          >
            {deleteMember.isPending ? "Deleting..." : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function MemberTagsEditor({ memberId }: { memberId: string }) {
  const qc = useQueryClient();
  const { data: allTags } = useQuery({ queryKey: ["tags"], queryFn: listTags });
  const { data: memberTags } = useQuery({
    queryKey: ["member", memberId, "tags"],
    queryFn: () => getMemberTags(memberId),
  });
  const setTags = useMutation({
    mutationFn: (tagIds: string[]) => setMemberTags(memberId, tagIds),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["member", memberId, "tags"] });
      // Tag-side member lists may now disagree with what's on the server,
      // since editing here is the symmetric counterpart of /v1/tags/{id}/members.
      qc.invalidateQueries({ queryKey: ["tags"] });
      setEditing(false);
      toast.success("Tags updated");
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Failed to update tags"),
  });
  const [editing, setEditing] = useState(false);
  const [draftIds, setDraftIds] = useState<string[]>([]);

  const currentIds = memberTags?.map((t) => t.id) ?? [];

  function startEdit() {
    setDraftIds(currentIds);
    setEditing(true);
  }

  function toggle(tagId: string) {
    setDraftIds((d) =>
      d.includes(tagId) ? d.filter((id) => id !== tagId) : [...d, tagId],
    );
  }

  if (!allTags || allTags.length === 0) {
    return null; // No tags configured — hide the section entirely.
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label className="text-xs text-muted-foreground">Tags</Label>
        {!editing ? (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 text-xs"
            onClick={startEdit}
          >
            Edit
          </Button>
        ) : (
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-xs"
              onClick={() => setEditing(false)}
              disabled={setTags.isPending}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              className="h-6 text-xs"
              onClick={() => setTags.mutate(draftIds)}
              disabled={setTags.isPending}
            >
              {setTags.isPending ? "Saving..." : "Save"}
            </Button>
          </div>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {editing
          ? allTags.map((t) => {
              const selected = draftIds.includes(t.id);
              return (
                <Badge
                  key={t.id}
                  variant={selected ? "default" : "outline"}
                  className="cursor-pointer gap-1.5"
                  onClick={() => toggle(t.id)}
                >
                  <ColorDot color={t.color} />
                  {t.name}
                </Badge>
              );
            })
          : memberTags && memberTags.length > 0
            ? memberTags.map((t) => (
                <Badge key={t.id} variant="outline" className="gap-1.5">
                  <ColorDot color={t.color} />
                  {t.name}
                </Badge>
              ))
            : (
              <span className="text-xs text-muted-foreground">
                No tags assigned.
              </span>
            )}
      </div>
    </div>
  );
}


function NotifyOnFrontEditor({ member }: { member: Member }) {
  const qc = useQueryClient();
  const { data: members } = useMembers();
  const { data: settings } = useQuery({
    queryKey: ["messages", "notify-settings", member.id],
    queryFn: () => getNotifySettings(member.id),
  });
  const save = useMutation({
    mutationFn: (body: {
      notify_on_front_global: boolean;
      notify_on_front_self: boolean;
      notify_on_front_member_ids: string[];
    }) => setNotifySettings(member.id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["messages", "notify-settings", member.id] });
      toast.success("Notification preferences updated");
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Failed to update"),
  });

  const [editing, setEditing] = useState(false);
  const [draftGlobal, setDraftGlobal] = useState(false);
  const [draftSelf, setDraftSelf] = useState(false);
  const [draftMemberIds, setDraftMemberIds] = useState<string[]>([]);

  if (!settings) return null;

  function startEdit() {
    if (!settings) return;
    setDraftGlobal(settings.notify_on_front_global);
    setDraftSelf(settings.notify_on_front_self);
    setDraftMemberIds(settings.notify_on_front_member_ids);
    setEditing(true);
  }

  function toggleMember(id: string) {
    setDraftMemberIds((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id],
    );
  }

  const otherMembers = (members ?? []).filter(
    (m) => m.id !== member.id && !m.is_custom_front,
  );

  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex items-center justify-between">
        <Label className="text-xs text-muted-foreground">
          On-front notifications
        </Label>
        {!editing ? (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 text-xs"
            onClick={startEdit}
          >
            Edit
          </Button>
        ) : (
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-xs"
              onClick={() => setEditing(false)}
              disabled={save.isPending}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              className="h-6 text-xs"
              onClick={() =>
                save.mutate(
                  {
                    notify_on_front_global: draftGlobal,
                    notify_on_front_self: draftSelf,
                    notify_on_front_member_ids: draftMemberIds,
                  },
                  { onSuccess: () => setEditing(false) },
                )
              }
              disabled={save.isPending}
            >
              {save.isPending ? "Saving..." : "Save"}
            </Button>
          </div>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        When this member starts fronting, prompt them about new messages on
        the boards they care about.
      </p>
      {!editing ? (
        <div className="space-y-1 text-sm">
          <div>
            Global board:{" "}
            <span className="font-medium">
              {settings.notify_on_front_global ? "yes" : "no"}
            </span>
          </div>
          <div>
            Their own wall:{" "}
            <span className="font-medium">
              {settings.notify_on_front_self ? "yes" : "no"}
            </span>
          </div>
          <div>
            Other members watched:{" "}
            <span className="font-medium">
              {settings.notify_on_front_member_ids.length === 0
                ? "none"
                : settings.notify_on_front_member_ids
                    .map(
                      (id) =>
                        otherMembers.find((m) => m.id === id)?.display_name ||
                        otherMembers.find((m) => m.id === id)?.name ||
                        "?",
                    )
                    .join(", ")}
            </span>
          </div>
        </div>
      ) : (
        <div className="space-y-2 text-sm">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={draftGlobal}
              onChange={(e) => setDraftGlobal(e.target.checked)}
            />
            Global board
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={draftSelf}
              onChange={(e) => setDraftSelf(e.target.checked)}
            />
            Their own wall
          </label>
          {otherMembers.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">Other members:</p>
              <div className="flex flex-wrap gap-1.5">
                {otherMembers.map((m) => {
                  const selected = draftMemberIds.includes(m.id);
                  return (
                    <Badge
                      key={m.id}
                      variant={selected ? "default" : "outline"}
                      className="cursor-pointer"
                      onClick={() => toggleMember(m.id)}
                    >
                      {m.display_name || m.name}
                    </Badge>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MemberView({
  member,
  onEdit,
  onClose,
}: {
  member: Member;
  onEdit: () => void;
  onClose: () => void;
}) {
  const { data: fields } = useCustomFields();
  const { data: values } = useMemberFieldValues(member.id);
  const { data: system } = useQuery({ queryKey: ["system", "me"], queryFn: getMySystem });
  const { data: safety } = useQuery({ queryKey: ["system-safety"], queryFn: getSystemSafety });
  const dateFormat = system?.date_format ?? "ymd";
  const [showRevisions, setShowRevisions] = useState(false);

  const fieldDisplay = useMemo(() => {
    if (!fields || !values) return [];
    const valMap: Record<string, string> = {};
    for (const v of values) {
      valMap[v.field_id] = typeof v.value === "object" && v.value !== null
        ? ((v.value as Record<string, unknown>).v as string ?? "")
        : String(v.value ?? "");
    }
    return fields
      .filter((f) => valMap[f.id])
      .map((f) => ({ name: f.name, value: valMap[f.id] }));
  }, [fields, values]);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <div className="flex items-center justify-between">
            <DialogTitle className="sr-only">{member.name}</DialogTitle>
            <div className="flex gap-1">
              <Button variant="ghost" size="sm" asChild>
                <Link to={`/journals?member_id=${member.id}`}>
                  <BookOpen className="h-3.5 w-3.5 mr-1" />
                  Journal
                </Link>
              </Button>
              <Button variant="ghost" size="sm" asChild>
                <Link to={`/messages?member=${member.id}`}>
                  <MessageSquare className="h-3.5 w-3.5 mr-1" />
                  Wall
                </Link>
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowRevisions((v) => !v)}
                aria-pressed={showRevisions}
                disabled={!member.has_bio_revisions}
                title={
                  member.has_bio_revisions ? "Bio history" : "No bio history"
                }
              >
                <History className="h-3.5 w-3.5 mr-1" />
                History
              </Button>
              <Button variant="ghost" size="sm" onClick={onEdit}>
                <Pencil className="h-3.5 w-3.5 mr-1" />
                Edit
              </Button>
            </div>
          </div>
        </DialogHeader>
        <div className="space-y-4">
          {/* Header: avatar + name + pronouns */}
          <div className="flex items-center gap-4">
            <Avatar className="size-16">
              {member.avatar_url && <AvatarImage src={member.avatar_url} />}
              <AvatarFallback
                className="text-xl"
                style={member.color ? { backgroundColor: member.color, color: "#fff" } : undefined}
              >
                {member.emoji?.trim() || member.name.charAt(0).toUpperCase()}
              </AvatarFallback>
            </Avatar>
            <div>
              <p className="text-lg font-semibold">
                {member.emoji && <span className="mr-1.5">{member.emoji}</span>}
                {member.display_name || member.name}
              </p>
              {member.display_name && (
                <p className="text-sm text-muted-foreground">{member.name}</p>
              )}
              {member.pronouns && (
                <p className="text-sm text-muted-foreground">{member.pronouns}</p>
              )}
              {member.birthday && (
                <p className="text-sm text-muted-foreground">{member.birthday}</p>
              )}
              {member.pluralkit_id && (
                <p className="text-xs text-muted-foreground font-mono">
                  PK: {member.pluralkit_id}
                </p>
              )}
            </div>
          </div>

          {/* Bio */}
          {member.description && (
            <div className="rounded-md border bg-muted/30 px-3 py-2">
              <Suspense fallback={<p className="text-sm text-muted-foreground">Loading...</p>}>
                <MarkdownPreview content={member.description} />
              </Suspense>
            </div>
          )}

          {/* Notes — scratchpad surface, deliberately separate from bio */}
          {member.note && (
            <div className="rounded-md border border-dashed bg-muted/20 px-3 py-2">
              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Notes
              </p>
              <Suspense fallback={<p className="text-sm text-muted-foreground">Loading...</p>}>
                <MarkdownPreview content={member.note} />
              </Suspense>
            </div>
          )}

          {/* Bio revisions */}
          {showRevisions && (
            <div className="rounded-md border px-3 py-2">
              <ContentRevisionList
                targetId={member.id}
                currentBody={member.description ?? ""}
                queryKey={["member", member.id, "revisions"]}
                list={listMemberBioRevisions}
                restore={restoreMemberBioRevision}
                pin={pinMemberBioRevision}
                unpin={unpinMemberBioRevision}
                safetyEnabled={
                  !!safety?.settings.applies_to_revisions &&
                  (safety?.settings.grace_period_days ?? 0) > 0
                }
                authTier={safety?.settings.auth_tier ?? "none"}
                invalidateOnRestore={[
                  ["members"],
                  ["member", member.id, "revisions"],
                  ["system-safety"],
                ]}
                emptyMessage="No bio revisions yet. Edits to the bio will appear here."
                dateFormat={dateFormat}
              />
            </div>
          )}

          {/* Custom fields */}
          {fieldDisplay.length > 0 && (
            <div className="space-y-1">
              {fieldDisplay.map((f) => (
                <div key={f.name} className="flex gap-2 text-sm">
                  <span className="text-muted-foreground">{f.name}:</span>
                  <span>{f.value}</span>
                </div>
              ))}
            </div>
          )}

          {/* Tags */}
          <MemberTagsEditor memberId={member.id} />

          {/* Notify-on-front settings */}
          <NotifyOnFrontEditor member={member} />
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function MembersPage() {
  const { data: members, isLoading } = useMembers();
  const { data: system } = useQuery({ queryKey: ["system", "me"], queryFn: getMySystem });
  const createMember = useCreateMember();
  const updateMember = useUpdateMember();
  const [showCreate, setShowCreate] = useState(false);
  const [viewing, setViewing] = useState<Member | null>(null);
  const [editing, setEditing] = useState<Member | null>(null);
  const [deleting, setDeleting] = useState<Member | null>(null);

  return (
    <>
      <PageHeader title="Members">
        <Button onClick={() => setShowCreate(true)}>Add member</Button>
      </PageHeader>

      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      ) : members && members.length > 0 ? (
        (() => {
          const realMembers = members.filter((m) => !m.is_custom_front);
          const customFronts = members.filter((m) => m.is_custom_front);
          return (
            <div className="space-y-8">
              <MemberGrid members={realMembers} onView={setViewing} />
              {customFronts.length > 0 && (
                <div className="space-y-3">
                  <div className="flex items-baseline justify-between">
                    <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
                      Custom fronts
                    </h2>
                    <p className="text-xs text-muted-foreground">
                      Non-counting fronting states (e.g. Asleep, Away).
                    </p>
                  </div>
                  <MemberGrid members={customFronts} onView={setViewing} />
                </div>
              )}
            </div>
          );
        })()
      ) : (
        <p className="text-muted-foreground">
          No members yet. Create your first member to get started.
        </p>
      )}

      {/* View dialog */}
      {viewing && (
        <MemberView
          member={viewing}
          onEdit={() => {
            setEditing(viewing);
            setViewing(null);
          }}
          onClose={() => setViewing(null)}
        />
      )}

      {/* Create dialog */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add member</DialogTitle>
          </DialogHeader>
          <MemberForm
            onSubmit={(data) =>
              createMember.mutate(data as MemberCreate, {
                onSuccess: () => setShowCreate(false),
              })
            }
            loading={createMember.isPending}
            submitLabel="Create"
          />
        </DialogContent>
      </Dialog>

      {/* Edit dialog */}
      <Dialog open={!!editing} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit member</DialogTitle>
          </DialogHeader>
          {editing && (
            <>
              <MemberForm
                initial={editing}
                onSubmit={(data) =>
                  updateMember.mutate(
                    { id: editing.id, data },
                    {
                      onSuccess: (updated) => {
                        setEditing(null);
                        setViewing(updated);
                      },
                    },
                  )
                }
                loading={updateMember.isPending}
                submitLabel="Save"
              />
              <MemberFieldValues memberId={editing.id} />
              <Button
                variant="destructive"
                size="sm"
                className="mt-2"
                onClick={() => {
                  setDeleting(editing);
                  setEditing(null);
                }}
              >
                Delete member
              </Button>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Delete confirm */}
      {deleting && (
        <DeleteMemberDialog
          member={deleting}
          level={system?.delete_confirmation ?? "none"}
          onOpenChange={(open) => !open && setDeleting(null)}
          onDeleted={() => setDeleting(null)}
        />
      )}
    </>
  );
}

function MemberGrid({
  members,
  onView,
}: {
  members: Member[];
  onView: (m: Member) => void;
}) {
  if (members.length === 0) return null;
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {members.map((m) => (
        <Card
          key={m.id}
          className="cursor-pointer transition-colors hover:bg-accent/50"
          onClick={() => onView(m)}
        >
          <CardContent className="flex items-center gap-3 p-4">
            <Avatar>
              {m.avatar_url && <AvatarImage src={m.avatar_url} />}
              <AvatarFallback
                style={m.color ? { backgroundColor: m.color, color: "#fff" } : undefined}
              >
                {m.emoji?.trim() || m.name.charAt(0).toUpperCase()}
              </AvatarFallback>
            </Avatar>
            <div className="min-w-0 flex-1">
              <p className="font-medium truncate">
                {m.emoji && <span className="mr-1.5">{m.emoji}</span>}
                {m.display_name || m.name}
              </p>
              {m.display_name && (
                <p className="text-xs text-muted-foreground truncate">{m.name}</p>
              )}
              {m.pronouns && (
                <p className="text-sm text-muted-foreground">{m.pronouns}</p>
              )}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
