import { type FormEvent, useState } from "react";
import {
  useGroups,
  useCreateGroup,
  useUpdateGroup,
  useDeleteGroup,
  useGroupMembers,
  useSetGroupMembers,
} from "@/hooks/use-groups";
import { useQuery } from "@tanstack/react-query";
import { getMySystem } from "@/lib/systems";
import { PageHeader } from "@/components/page-header";
import { ColorDot } from "@/components/color-dot";
import { MemberSelect } from "@/components/member-select";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
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
import type { Group } from "@/types/api";

function GroupMembersEditor({ groupId }: { groupId: string }) {
  const { data: groupMembers } = useGroupMembers(groupId);
  const setMembers = useSetGroupMembers();
  const [selected, setSelected] = useState<string[] | null>(null);

  const currentIds = groupMembers?.map((m) => m.id) ?? [];
  const editing = selected !== null;
  const displayIds = selected ?? currentIds;

  return (
    <div className="space-y-2">
      <Label>Members</Label>
      <MemberSelect selected={displayIds} onChange={setSelected} />
      {editing && (
        <Button
          size="sm"
          onClick={() =>
            setMembers.mutate(
              { id: groupId, memberIds: selected },
              { onSuccess: () => setSelected(null) },
            )
          }
          disabled={setMembers.isPending}
        >
          {setMembers.isPending ? "Saving..." : "Save members"}
        </Button>
      )}
    </div>
  );
}

export function GroupsPage() {
  const { data: groups, isLoading } = useGroups();
  const createGroup = useCreateGroup();
  const updateGroup = useUpdateGroup();
  const deleteGroup = useDeleteGroup();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Group | null>(null);
  const [deleting, setDeleting] = useState<Group | null>(null);

  const [name, setName] = useState("");
  const [color, setColor] = useState("#6366f1");

  function resetForm() {
    setName("");
    setColor("#6366f1");
  }

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    createGroup.mutate(
      { name, color: color || null },
      {
        onSuccess: () => {
          setShowCreate(false);
          resetForm();
        },
      },
    );
  }

  function handleUpdate(e: FormEvent) {
    e.preventDefault();
    if (!editing) return;
    updateGroup.mutate(
      { id: editing.id, data: { name, color: color || null } },
      { onSuccess: () => setEditing(null) },
    );
  }

  function openEdit(group: Group) {
    setName(group.name);
    setColor(group.color ?? "#6366f1");
    setEditing(group);
  }

  return (
    <>
      <PageHeader title="Groups">
        <Button
          onClick={() => {
            resetForm();
            setShowCreate(true);
          }}
        >
          Add group
        </Button>
      </PageHeader>

      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      ) : groups && groups.length > 0 ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {groups.map((g) => (
            <Card
              key={g.id}
              className="cursor-pointer transition-colors hover:bg-accent/50"
              onClick={() => openEdit(g)}
            >
              <CardContent className="flex items-center gap-3 p-4">
                <ColorDot color={g.color} className="h-4 w-4" />
                <p className="font-medium">{g.name}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <p className="text-muted-foreground">
          No groups yet. Groups let you organize members.
        </p>
      )}

      {/* Create dialog */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add group</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleCreate} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="group-create-name">Name</Label>
              <Input id="group-create-name" value={name} onChange={(e) => setName(e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="group-create-color">Color</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="group-create-color"
                  type="color"
                  value={color}
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
            <DialogFooter>
              <Button type="submit" disabled={createGroup.isPending || !name}>
                {createGroup.isPending ? "Creating..." : "Create"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Edit dialog */}
      <Dialog open={!!editing} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit group</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleUpdate} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="group-edit-name">Name</Label>
              <Input id="group-edit-name" value={name} onChange={(e) => setName(e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="group-edit-color">Color</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="group-edit-color"
                  type="color"
                  value={color}
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
            <DialogFooter>
              <Button type="submit" disabled={updateGroup.isPending || !name}>
                {updateGroup.isPending ? "Saving..." : "Save"}
              </Button>
            </DialogFooter>
          </form>

          {editing && <GroupMembersEditor groupId={editing.id} />}

          <Button
            variant="destructive"
            size="sm"
            className="mt-2"
            onClick={() => {
              setDeleting(editing);
              setEditing(null);
            }}
          >
            Delete group
          </Button>
        </DialogContent>
      </Dialog>

      {/* Delete confirm */}
      <DestructiveConfirmDialog
        open={!!deleting}
        onOpenChange={(open) => !open && setDeleting(null)}
        title="Delete group"
        description={`Are you sure you want to delete "${deleting?.name}"?`}
        tier={system?.delete_confirmation ?? "none"}
        onConfirm={(confirm) =>
          deleting &&
          deleteGroup.mutate(
            { id: deleting.id, confirm },
            { onSuccess: () => setDeleting(null) },
          )
        }
        loading={deleteGroup.isPending}
      />
    </>
  );
}
