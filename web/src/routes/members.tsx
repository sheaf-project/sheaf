import { type FormEvent, useState } from "react";
import { useMembers, useCreateMember, useDeleteMember, useUpdateMember } from "@/hooks/use-members";
import { PageHeader } from "@/components/page-header";
import { ColorDot } from "@/components/color-dot";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { Member, MemberCreate, MemberUpdate } from "@/types/api";

function MemberForm({
  initial,
  onSubmit,
  loading,
  submitLabel,
}: {
  initial?: Partial<MemberCreate>;
  onSubmit: (data: MemberCreate | MemberUpdate) => void;
  loading: boolean;
  submitLabel: string;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [pronouns, setPronouns] = useState(initial?.pronouns ?? "");
  const [color, setColor] = useState(initial?.color ?? "#6366f1");
  const [description, setDescription] = useState(initial?.description ?? "");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      name,
      pronouns: pronouns || null,
      color: color || null,
      description: description || null,
    });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
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
        <Label>Description</Label>
        <Input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Optional"
        />
      </div>
      <DialogFooter>
        <Button type="submit" disabled={loading || !name}>
          {loading ? "Saving..." : submitLabel}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function MembersPage() {
  const { data: members, isLoading } = useMembers();
  const createMember = useCreateMember();
  const updateMember = useUpdateMember();
  const deleteMember = useDeleteMember();
  const [showCreate, setShowCreate] = useState(false);
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
              onClick={() => setEditing(m)}
            >
              <CardContent className="flex items-center gap-3 p-4">
                <ColorDot color={m.color} className="h-4 w-4" />
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
                    { onSuccess: () => setEditing(null) },
                  )
                }
                loading={updateMember.isPending}
                submitLabel="Save"
              />
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
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(open) => !open && setDeleting(null)}
        title="Delete member"
        description={`Are you sure you want to delete "${deleting?.name}"? This cannot be undone.`}
        onConfirm={() =>
          deleting &&
          deleteMember.mutate(deleting.id, {
            onSuccess: () => setDeleting(null),
          })
        }
        loading={deleteMember.isPending}
      />
    </>
  );
}
