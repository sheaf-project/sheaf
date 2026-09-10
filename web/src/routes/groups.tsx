import { type FormEvent, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, ChevronUp, GripVertical } from "lucide-react";
import {
  useGroups,
  useCreateGroup,
  useUpdateGroup,
  useDeleteGroup,
  useGroupMembers,
  useReorderGroups,
  useSetGroupMembers,
} from "@/hooks/use-groups";
import { useQuery } from "@tanstack/react-query";
import { getMySystem } from "@/lib/systems";
import { getSystemSafety } from "@/lib/system-safety";
import { isStepUpRequiredError, showApiErrorToast } from "@/lib/api-errors";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import {
  buildGroupTree,
  compareGroups,
  flattenGroupTree,
  getDescendantIds,
} from "@/lib/group-tree";
import { PageHeader } from "@/components/page-header";
import { ColorDot } from "@/components/color-dot";
import { MemberSelect } from "@/components/member-select";
import { RelationshipsEditor } from "@/components/relationships-editor";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { PendingDeleteBadge } from "@/components/pending-delete-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  PrivacyLevelSelect,
  PublishingOffNote,
} from "@/components/privacy-level-select";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type {
  DeleteConfirmation,
  DestructiveConfirm,
  Group,
  GroupCreate,
  GroupUpdate,
  PrivacyLevel,
} from "@/types/api";

/** Permission, never a promise: a public group still has to be in a view that
 *  was told to show groups before anyone sees it. One line, because a
 *  paragraph next to a select is a paragraph nobody reads. */
const GROUP_PRIVACY_HELP =
  "Public means this group can appear on shared views and public profiles, but only on a view you set to show groups.";

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
  const reorderGroups = useReorderGroups();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  // Read only to pick the re-auth tier for a staged privacy raise; both are
  // cached queries the rest of the app already keeps warm.
  const { data: safety } = useQuery({
    queryKey: ["system-safety"],
    queryFn: getSystemSafety,
  });
  const { formatDate } = useDateFormatters();
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Group | null>(null);
  const [deleting, setDeleting] = useState<Group | null>(null);

  const [name, setName] = useState("");
  const [color, setColor] = useState("#6366f1");
  const [parentId, setParentId] = useState("");
  // A group says something about everyone in it, so a new one starts private
  // no matter what its members are set to.
  const [privacy, setPrivacy] = useState<PrivacyLevel>("private");
  // The bounced save, held so the step-up dialog can retry the exact same
  // payload with credentials attached rather than reconstructing it. Creating a
  // group already public goes through the same server-side door as raising one,
  // so both shapes land here and the dialog retries whichever it was holding.
  const [stepUp, setStepUp] = useState<
    | { kind: "create"; data: GroupCreate; tier: DeleteConfirmation }
    | { kind: "update"; id: string; data: GroupUpdate; tier: DeleteConfirmation }
    | null
  >(null);

  /** The tier to prompt at, which is the safety category's own setting when it
   *  has one. Same fallback chain the other privacy surfaces use. */
  const stepUpTier: DeleteConfirmation =
    safety?.settings.auth_tier ?? system?.delete_confirmation ?? "password";

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | "root" | null>(null);

  const allGroups = useMemo(() => groups ?? [], [groups]);
  const rows = useMemo(
    () => flattenGroupTree(buildGroupTree(allGroups), collapsed),
    [allGroups, collapsed],
  );

  // The dialog holds the group it was opened with; read the live row back out
  // so a staged raise shows up the moment the list refetches.
  const editingLive = editing
    ? (allGroups.find((g) => g.id === editing.id) ?? editing)
    : null;

  function resetForm() {
    setName("");
    setColor("#6366f1");
    setParentId("");
    setPrivacy("private");
  }

  /** Create the group.
   *
   * Sent without credentials first, exactly as the edit form is: a new group
   * born public is the same exposure as raising an existing one, so the server
   * answers it with the same 400 asking for step-up. Without this the "new
   * group, privacy: Public" path would be a dead end, and worse, a way around
   * the gate the edit form honours.
   */
  function handleCreate(e: FormEvent) {
    e.preventDefault();
    const data: GroupCreate = {
      name,
      color: color || null,
      parent_id: parentId || null,
      privacy,
    };
    createGroup.mutate(
      { data, skipErrorToast: true },
      {
        onSuccess: () => {
          setShowCreate(false);
          resetForm();
        },
        onError: (err) => {
          if (isStepUpRequiredError(err)) {
            setStepUp({ kind: "create", data, tier: stepUpTier });
            return;
          }
          showApiErrorToast(err, "Couldn't create this group.", { force: true });
        },
      },
    );
  }

  /** Save the edit form.
   *
   * Sent without credentials first: only a raise that would actually put the
   * group in front of someone is answered with a 400 asking for them, so the
   * common case stays a single click and the prompt only appears when it is
   * genuinely a step-up. Lowering is never gated.
   */
  function handleUpdate(e: FormEvent) {
    e.preventDefault();
    if (!editing) return;
    const id = editing.id;
    const data: GroupUpdate = {
      name,
      color: color || null,
      parent_id: parentId || null,
      privacy,
    };
    updateGroup.mutate(
      { id, data, skipErrorToast: true },
      {
        onSuccess: () => setEditing(null),
        onError: (err) => {
          if (isStepUpRequiredError(err)) {
            setStepUp({ kind: "update", id, data, tier: stepUpTier });
            return;
          }
          showApiErrorToast(err, "Couldn't update this group.", { force: true });
        },
      },
    );
  }

  function openEdit(group: Group) {
    setName(group.name);
    setColor(group.color ?? "#6366f1");
    setParentId(group.parent_id ?? "");
    setPrivacy(group.privacy);
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

  /** This group's siblings (same parent), in the order the tree renders
   *  them. */
  function siblingsOf(g: Group): Group[] {
    return allGroups
      .filter((s) => (s.parent_id ?? null) === (g.parent_id ?? null))
      .sort(compareGroups);
  }

  /** Move a group one place among its siblings.
   *
   * Sends the FULL flat ordering (every group, depth-first, with the pair
   * swapped) so the server's order = list-index assignment reproduces
   * exactly what is on screen. Arrows rather than drag because drag on
   * these rows already means reparenting.
   */
  function moveGroup(g: Group, delta: -1 | 1) {
    const siblings = siblingsOf(g);
    const i = siblings.findIndex((s) => s.id === g.id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= siblings.length) return;
    const swapped = [...siblings];
    [swapped[i], swapped[j]] = [swapped[j], swapped[i]];
    // Rebuild the tree with the swapped run's new positions patched in,
    // then flatten it (nothing collapsed) into the id list to send.
    const position = new Map(swapped.map((s, idx) => [s.id, idx]));
    const patched = allGroups.map((grp) =>
      position.has(grp.id) ? { ...grp, order: position.get(grp.id)! } : grp,
    );
    const ids = flattenGroupTree(buildGroupTree(patched), new Set()).map(
      (r) => r.group.id,
    );
    reorderGroups.mutate(ids, {
      onError: (e) => showApiErrorToast(e, "Couldn't reorder groups."),
    });
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
            onDrop={(e) => {
              e.preventDefault();
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
            const sibIndex = siblingsOf(g).findIndex((s) => s.id === g.id);
            const sibCount = siblingsOf(g).length;
            return (
              <div
                key={g.id}
                draggable
                onDragStart={(e) => {
                  // Populating the drag data store is REQUIRED for the drag
                  // to start at all in Firefox (Chrome tolerates an empty
                  // store) - without this line drag-to-reparent is simply
                  // inert there. The payload itself is unused; draggingId
                  // in React state is the real carrier.
                  e.dataTransfer.setData("text/plain", g.id);
                  e.dataTransfer.effectAllowed = "move";
                  // Deferred one tick: setting state here re-renders the row
                  // (opacity) and mounts the top-level drop zone, and Chromium
                  // cancels a drag whose source DOM mutates during dragstart.
                  // After the current tick the drag has genuinely begun and
                  // mutations are fine.
                  setTimeout(() => setDraggingId(g.id), 0);
                }}
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
                onDrop={(e) => {
                  e.preventDefault();
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
                {/* Reorder arrows move the group among its SIBLINGS only;
                    moving between parents is what the drag gesture does. */}
                <button
                  type="button"
                  onClick={() => moveGroup(g, -1)}
                  disabled={sibIndex <= 0 || reorderGroups.isPending}
                  className="shrink-0 text-muted-foreground hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
                  aria-label="Move up"
                >
                  <ChevronUp className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={() => moveGroup(g, 1)}
                  disabled={
                    sibIndex >= sibCount - 1 || reorderGroups.isPending
                  }
                  className="shrink-0 text-muted-foreground hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
                  aria-label="Move down"
                >
                  <ChevronDown className="h-4 w-4" />
                </button>
                {/* Private is the default and the safe state, so saying so on
                    every row would be noise that trains people to stop reading
                    the badge that matters. */}
                {g.privacy !== "private" && (
                  <Badge variant="outline" className="shrink-0 text-[10px]">
                    {g.privacy}
                  </Badge>
                )}
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
            <div className="space-y-2">
              <Label htmlFor="group-create-privacy">Privacy</Label>
              {/* No stored record yet, so Public is a raise from nothing. */}
              <PrivacyLevelSelect
                id="group-create-privacy"
                value={privacy}
                onValueChange={setPrivacy}
              />
              <p className="text-xs text-muted-foreground">
                {GROUP_PRIVACY_HELP}
              </p>
              <PublishingOffNote />
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
            <div className="space-y-2">
              <Label htmlFor="group-edit-privacy">Privacy</Label>
              {/* `savedValue` is the group's LIVE level, not `privacy` (which
                  the select is busy changing) and not a staged
                  `pending_privacy`: the backend compares a request against the
                  stored level, so a group already public keeps Public offered
                  and can still be lowered. */}
              <PrivacyLevelSelect
                id="group-edit-privacy"
                value={privacy}
                onValueChange={setPrivacy}
                savedValue={editingLive?.privacy}
              />
              <p className="text-xs text-muted-foreground">
                {GROUP_PRIVACY_HELP}
              </p>
              <PublishingOffNote savedValue={editingLive?.privacy} />
              {/* A raise is staged, so the live level is still the old one
                  until the grace window elapses; say which is which. */}
              {editingLive?.privacy_activates_at && (
                <p className="text-[11px] text-amber-600 dark:text-amber-500">
                  {editingLive.pending_privacy ?? "public"} - activates{" "}
                  {formatDate(editingLive.privacy_activates_at)}. Until then
                  this stays {editingLive.privacy}.
                </p>
              )}
            </div>
            <DialogFooter>
              <Button type="submit" disabled={updateGroup.isPending || !name}>
                {updateGroup.isPending ? "Saving..." : "Save"}
              </Button>
            </DialogFooter>
          </form>

          {editing && <GroupMembersEditor groupId={editing.id} />}

          {editing && (
            <RelationshipsEditor
              nodeId={editing.id}
              scope="group"
              nodes={allGroups
                .filter((g) => g.id !== editing.id)
                .map((g) => ({ id: g.id, name: g.name }))}
            />
          )}

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

      {/* Step-up for a public group the server bounced, whether it was a raise
          on an existing one or a new one born public. Retries the same save
          with credentials attached; with a grace period set it is still staged
          after. */}
      <DestructiveConfirmDialog
        open={!!stepUp}
        onOpenChange={(open) => !open && setStepUp(null)}
        title="Confirm public visibility change"
        description="Publishing this group can reveal it, and everyone shown in it, through an existing public profile or share link. Confirm now; if you have a grace period set, it takes effect after your System Safety window."
        tier={stepUp?.tier ?? "none"}
        actionLabel="Confirm change"
        actionLabelLoading="Saving..."
        loading={
          stepUp?.kind === "create"
            ? createGroup.isPending
            : updateGroup.isPending
        }
        onConfirm={(confirm?: DestructiveConfirm) => {
          if (!stepUp) return;
          if (stepUp.kind === "create") {
            createGroup.mutate(
              { data: { ...stepUp.data, ...confirm } },
              {
                onSuccess: () => {
                  setStepUp(null);
                  setShowCreate(false);
                  resetForm();
                },
              },
            );
            return;
          }
          updateGroup.mutate(
            { id: stepUp.id, data: { ...stepUp.data, ...confirm } },
            {
              onSuccess: () => {
                setStepUp(null);
                setEditing(null);
              },
            },
          );
        }}
      />

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
