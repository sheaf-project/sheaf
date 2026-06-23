import { type FormEvent, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, GripVertical } from "lucide-react";
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
import { showApiErrorToast } from "@/lib/api-errors";
import {
  buildGroupTree,
  flattenGroupTree,
  getDescendantIds,
} from "@/lib/group-tree";
import { PageHeader } from "@/components/page-header";
import { ColorDot } from "@/components/color-dot";
import { MemberSelect } from "@/components/member-select";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { PendingDeleteBadge } from "@/components/pending-delete-badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
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

/** A parent-group <select>; excludes self + descendants so you can't pick a
 *  parent that would create a cycle. */
function ParentSelect({
  groups,
  exclude,
  value,
  onChange,
  id,
}: {
  groups: Group[];
  exclude: Set<string>;
  value: string;
  onChange: (v: string) => void;
  id: string;
}) {
  const rows = useMemo(
    () =>
      flattenGroupTree(buildGroupTree(groups), new Set()).filter(
        (r) => !exclude.has(r.group.id),
      ),
    [groups, exclude],
  );
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
    >
      <option value="">Top level (no parent)</option>
      {rows.map((r) => (
        <option key={r.group.id} value={r.group.id}>
          {" ".repeat(r.depth)}
          {r.group.name}
        </option>
      ))}
    </select>
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
  const [parentId, setParentId] = useState("");

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | "root" | null>(null);

  const allGroups = useMemo(() => groups ?? [], [groups]);
  const rows = useMemo(
    () => flattenGroupTree(buildGroupTree(allGroups), collapsed),
    [allGroups, collapsed],
  );

  function resetForm() {
    setName("");
    setColor("#6366f1");
    setParentId("");
  }

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    createGroup.mutate(
      { name, color: color || null, parent_id: parentId || null },
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
      {
        id: editing.id,
        data: { name, color: color || null, parent_id: parentId || null },
      },
      { onSuccess: () => setEditing(null) },
    );
  }

  function openEdit(group: Group) {
    setName(group.name);
    setColor(group.color ?? "#6366f1");
    setParentId(group.parent_id ?? "");
    setEditing(group);
  }

  function toggleCollapse(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  /** True when `targetId` would be an illegal parent for the dragged group
   *  (itself or one of its descendants). */
  function isInvalidDrop(targetId: string): boolean {
    if (!draggingId) return true;
    if (targetId === draggingId) return true;
    return getDescendantIds(draggingId, allGroups).has(targetId);
  }

  function reparent(childId: string, newParentId: string | null) {
    const child = allGroups.find((g) => g.id === childId);
    if (!child || (child.parent_id ?? null) === newParentId) return;
    updateGroup.mutate(
      { id: childId, data: { parent_id: newParentId } },
      { onError: (e) => showApiErrorToast(e, "Couldn't move group.") },
    );
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
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : rows.length > 0 ? (
        <div className="space-y-1">
          {/* Top-level drop zone: drop here to un-nest a group. */}
          <div
            onDragOver={(e) => {
              if (draggingId) {
                e.preventDefault();
                setDropTarget("root");
              }
            }}
            onDragLeave={() => setDropTarget((t) => (t === "root" ? null : t))}
            onDrop={() => {
              if (draggingId) reparent(draggingId, null);
              setDraggingId(null);
              setDropTarget(null);
            }}
            className={cn(
              "rounded-md border border-dashed px-3 py-1.5 text-xs text-muted-foreground transition-colors",
              dropTarget === "root"
                ? "border-primary bg-primary/5 text-primary"
                : "border-transparent",
              draggingId ? "border-border" : "hidden",
            )}
          >
            Drop here to move to the top level
          </div>

          {rows.map(({ group: g, depth, hasChildren }) => {
            const invalid = draggingId ? isInvalidDrop(g.id) : false;
            return (
              <div
                key={g.id}
                draggable
                onDragStart={() => setDraggingId(g.id)}
                onDragEnd={() => {
                  setDraggingId(null);
                  setDropTarget(null);
                }}
                onDragOver={(e) => {
                  if (draggingId && !invalid) {
                    e.preventDefault();
                    setDropTarget(g.id);
                  }
                }}
                onDragLeave={() =>
                  setDropTarget((t) => (t === g.id ? null : t))
                }
                onDrop={() => {
                  if (draggingId && !invalid) reparent(draggingId, g.id);
                  setDraggingId(null);
                  setDropTarget(null);
                }}
                style={{ marginLeft: depth * 20 }}
                className={cn(
                  "group flex items-center gap-2 rounded-md border bg-card px-3 py-2 transition-colors",
                  depth > 0 && "border-l-2",
                  dropTarget === g.id && "border-primary bg-primary/5",
                  draggingId === g.id && "opacity-50",
                  g.pending_delete_at && "opacity-60",
                )}
              >
                <GripVertical className="h-4 w-4 shrink-0 cursor-grab text-muted-foreground" />
                {hasChildren ? (
                  <button
                    type="button"
                    onClick={() => toggleCollapse(g.id)}
                    className="shrink-0 text-muted-foreground hover:text-foreground"
                    aria-label={collapsed.has(g.id) ? "Expand" : "Collapse"}
                  >
                    {collapsed.has(g.id) ? (
                      <ChevronRight className="h-4 w-4" />
                    ) : (
                      <ChevronDown className="h-4 w-4" />
                    )}
                  </button>
                ) : (
                  <span className="w-4 shrink-0" />
                )}
                <ColorDot color={g.color} className="h-3.5 w-3.5 shrink-0" />
                <button
                  type="button"
                  onClick={() => openEdit(g)}
                  className="min-w-0 flex-1 truncate text-left font-medium hover:underline"
                >
                  {g.name}
                </button>
                <PendingDeleteBadge finalizeAt={g.pending_delete_at} />
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-muted-foreground">
          No groups yet. Groups let you organize members, and can be nested.
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
              <Label htmlFor="group-create-parent">Parent group</Label>
              <ParentSelect
                id="group-create-parent"
                groups={allGroups}
                exclude={new Set()}
                value={parentId}
                onChange={setParentId}
              />
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
              <Label htmlFor="group-edit-parent">Parent group</Label>
              <ParentSelect
                id="group-edit-parent"
                groups={allGroups}
                exclude={
                  editing
                    ? new Set([
                        editing.id,
                        ...getDescendantIds(editing.id, allGroups),
                      ])
                    : new Set()
                }
                value={parentId}
                onChange={setParentId}
              />
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
        description={`Are you sure you want to delete "${deleting?.name}"? Any subgroups move up to its parent.`}
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
