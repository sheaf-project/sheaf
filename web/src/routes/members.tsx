import { type FormEvent, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useMembers, useCreateMember, useDeleteMember, useUpdateMember } from "@/hooks/use-members";
import { useCustomFields, useMemberFieldValues, useSetMemberFieldValues } from "@/hooks/use-custom-fields";
import { getMySystem } from "@/lib/systems";
import { AvatarUpload } from "@/components/avatar-upload";
import { BioEditor, MarkdownPreview } from "@/components/bio-editor";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { DatePicker } from "@/components/date-picker";
import { PageHeader } from "@/components/page-header";
import { Pencil } from "lucide-react";
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
  const [avatarUrl, setAvatarUrl] = useState(initial?.avatar_url ?? null);
  const [pronouns, setPronouns] = useState(initial?.pronouns ?? "");
  const [color, setColor] = useState(initial?.color ?? "#6366f1");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [birthday, setBirthday] = useState(initial?.birthday ?? "");
  const [privacy, setPrivacy] = useState<PrivacyLevel>(initial?.privacy ?? "private");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      name,
      avatar_url: avatarUrl,
      pronouns: pronouns || null,
      color: color || null,
      description: description || null,
      birthday: birthday || null,
      privacy,
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
        <Label>Name</Label>
        <Input value={name} onChange={(e) => setName(e.target.value)} required />
      </div>
      <div className="space-y-2">
        <Label>Pronouns</Label>
        <Input
          value={pronouns}
          onChange={(e) => setPronouns(e.target.value)}
          placeholder="e.g. she/her"
        />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>Color</Label>
          <div className="flex items-center gap-2">
            <Input
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
        <BioEditor value={description} onChange={setDescription} />
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
          <Label className="text-xs">{f.name}</Label>
          <Input
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
                <Label className="text-sm">Password</Label>
                <Input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                />
              </div>
            )}
            {needsTotp && (
              <div className="space-y-1">
                <Label className="text-sm">TOTP code</Label>
                <Input
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                  placeholder="6-digit code"
                  maxLength={6}
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
            <Button variant="ghost" size="sm" onClick={onEdit}>
              <Pencil className="h-3.5 w-3.5 mr-1" />
              Edit
            </Button>
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
                {member.name.charAt(0).toUpperCase()}
              </AvatarFallback>
            </Avatar>
            <div>
              <p className="text-lg font-semibold">{member.name}</p>
              {member.pronouns && (
                <p className="text-sm text-muted-foreground">{member.pronouns}</p>
              )}
              {member.birthday && (
                <p className="text-sm text-muted-foreground">{member.birthday}</p>
              )}
            </div>
          </div>

          {/* Bio */}
          {member.description && (
            <div className="rounded-md border bg-muted/30 px-3 py-2">
              <MarkdownPreview content={member.description} />
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
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {members.map((m) => (
            <Card
              key={m.id}
              className="cursor-pointer transition-colors hover:bg-accent/50"
              onClick={() => setViewing(m)}
            >
              <CardContent className="flex items-center gap-3 p-4">
                <Avatar>
                  {m.avatar_url && <AvatarImage src={m.avatar_url} />}
                  <AvatarFallback
                    style={m.color ? { backgroundColor: m.color, color: "#fff" } : undefined}
                  >
                    {m.name.charAt(0).toUpperCase()}
                  </AvatarFallback>
                </Avatar>
                <div className="min-w-0 flex-1">
                  <p className="font-medium truncate">{m.name}</p>
                  {m.pronouns && (
                    <p className="text-sm text-muted-foreground">{m.pronouns}</p>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
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
