import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ColorDot } from "@/components/color-dot";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useCreateGroupRelationship,
  useCreateMemberRelationship,
  useDeleteGroupRelationship,
  useDeleteMemberRelationship,
  useGroupRelationships,
  useMemberRelationships,
  useRelationshipGraph,
  useRelationshipTypes,
  useUpdateGroupRelationship,
  useUpdateMemberRelationship,
} from "@/hooks/use-relationships";
import { RelationshipGraphCanvas } from "@/components/relationship-graph";
import { RelationshipPrivacyControl } from "@/components/relationship-privacy-control";
import { RelationshipTypeDialog } from "@/components/relationship-type-dialog";
import type { GraphEdge } from "@/lib/relationship-graph";
import {
  EDGE_VISIBILITY_HELP,
  EDGE_VISIBILITY_LEVELS,
} from "@/lib/relationship-privacy";
import type {
  PrivacyLevel,
  RelationshipEdgeCreate,
  RelationshipGraph,
  RelationshipGraphEdge,
} from "@/types/api";

/**
 * The owner's view of the graph: the shared renderer, plus everything only an
 * owner gets. The picture itself (layout, pan, zoom, hit testing) lives in
 * `RelationshipGraphCanvas`, which knows nothing about edit modes or dialogs;
 * this holds the modes and the dialogs and hands the renderer two arrays and
 * two callbacks.
 */
function GraphCanvas({
  graph,
  scope,
  editMode,
}: {
  graph: RelationshipGraph;
  scope: "members" | "groups";
  /** Off: the graph is something to read, and an edge opens read-only. On:
   *  relationships can be added, reoriented, republished and removed. */
  editMode: boolean;
}) {
  // "Add relationship" mode: click a source node then a target node.
  const [addMode, setAddMode] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [targetId, setTargetId] = useState<string | null>(null);
  // The edge whose dialog is open.
  const [openEdgeId, setOpenEdgeId] = useState<string | null>(null);
  const nodeNoun = scope === "members" ? "member" : "group";
  const { data: types } = useRelationshipTypes();

  // Add mode is derived, not stored twice: leaving edit mode suspends it
  // rather than needing an effect to go and switch it off.
  const adding = editMode && addMode;

  // Edge colour is a client-side join: the graph payload carries the type id,
  // the type carries the colour. The renderer only relays the graph out when
  // its shape changes, so recolouring a type repaints without disturbing it.
  const edges = useMemo<GraphEdge[]>(() => {
    const colorByType = new Map((types ?? []).map((t) => [t.id, t.color] as const));
    return graph.edges.map((e) => ({
      ...e,
      color: colorByType.get(e.relationship_type_id) ?? null,
    }));
  }, [graph, types]);

  function toggleAddMode() {
    setAddMode((m) => !m);
    setPendingId(null);
    setTargetId(null);
  }

  /** Add mode's two-node pick: source, then a distinct target opens the
   *  dialog. Clicking the picked node again puts it back. */
  function onNodePicked(id: string) {
    if (!pendingId) setPendingId(id);
    else if (pendingId === id) setPendingId(null);
    else setTargetId(id);
  }

  const nodeName = (id: string) =>
    graph.nodes.find((n) => n.id === id)?.name ?? id.slice(0, 8);

  // The edge dialog reads the payload edge, not the renderer's copy: the raw
  // edge keeps the labels, mutual flag and type id a drawing has no use for. An
  // edge that disappears (removed here or elsewhere) simply closes the dialog.
  const openEdge = graph.edges.find((e) => e.id === openEdgeId) ?? null;
  const pending = pendingId ? graph.nodes.find((n) => n.id === pendingId) : null;
  const target = targetId ? graph.nodes.find((n) => n.id === targetId) : null;

  return (
    <>
      <RelationshipGraphCanvas
        nodes={graph.nodes}
        edges={edges}
        // Picking an edge is picking a relationship, which is a thing you can
        // do while just reading the graph; only what the dialog then offers
        // depends on edit mode. Add mode is the exception: there, every click
        // is part of "pick two nodes".
        onEdgeClick={adding ? undefined : setOpenEdgeId}
        onNodeClick={adding ? onNodePicked : undefined}
        nodePress={adding ? "pick" : "drag"}
        highlightNodeId={pendingId}
        activeEdgeId={openEdgeId}
        toolbar={
          editMode && (
            <Button
              variant={adding ? "default" : "outline"}
              size="sm"
              onClick={toggleAddMode}
            >
              {adding ? "Adding relationships" : "Add relationship"}
            </Button>
          )
        }
        overlay={
          adding && (
            <div className="absolute left-2 top-2 z-10 rounded-md border bg-background/90 px-2 py-1 text-xs text-muted-foreground">
              {pending
                ? `${pending.name} selected. Click another ${nodeNoun} to connect them.`
                : `Click a ${nodeNoun} to start.`}
            </div>
          )
        }
      />
      {adding && pending && target && (
        <AddEdgeDialog
          scope={scope}
          source={{ id: pending.id, name: pending.name }}
          target={{ id: target.id, name: target.name }}
          onClose={() => {
            setTargetId(null);
            setPendingId(null);
          }}
        />
      )}
      {openEdge && (
        <EdgeDialog
          scope={scope}
          edge={openEdge}
          sourceName={nodeName(openEdge.source_id)}
          targetName={nodeName(openEdge.target_id)}
          editable={editMode}
          onClose={() => setOpenEdgeId(null)}
        />
      )}
    </>
  );
}

/**
 * One existing edge, opened by clicking it on the graph. The graph is where
 * relationships are actually looked at, so it is also where they are managed:
 * this is the edge's whole management surface (privacy, direction, mutual,
 * removal), the same set of things the per-member editor offers, on the same
 * shared privacy control.
 *
 * One component, two presentations. Without edit mode it states what the edge
 * is and stops there - no select, no buttons, nothing that can change anything
 * by being clicked at.
 */
function EdgeDialog({
  scope,
  edge,
  sourceName,
  targetName,
  editable,
  onClose,
}: {
  scope: "members" | "groups";
  edge: RelationshipGraphEdge;
  sourceName: string;
  targetName: string;
  editable: boolean;
  onClose: () => void;
}) {
  const isMember = scope === "members";
  const nodeNoun = isMember ? "member" : "group";
  const { data: types } = useRelationshipTypes();
  const type = types?.find((t) => t.id === edge.relationship_type_id);

  // The graph payload carries no privacy fields, and that is fine: the
  // per-endpoint relationship list does, it is already a cached query, and the
  // same key prefix invalidates it whenever anything about an edge changes.
  // Either endpoint's list holds this edge, so one fetch answers it - cheaper
  // and less to keep in step than widening the graph response.
  const memberRows = useMemberRelationships(isMember ? edge.source_id : null);
  const groupRows = useGroupRelationships(isMember ? null : edge.source_id);
  const rows = isMember ? memberRows.data : groupRows.data;
  const row = rows?.find((e) => e.id === edge.id);

  const updateMember = useUpdateMemberRelationship();
  const updateGroup = useUpdateGroupRelationship();
  const update = isMember ? updateMember : updateGroup;
  const deleteMember = useDeleteMemberRelationship();
  const deleteGroup = useDeleteGroupRelationship();
  const remove = isMember ? deleteMember : deleteGroup;

  // A symmetric type has no direction to reverse, and a mutual either-edge
  // reads the same label at both ends, so neither offers a flip.
  const canFlip = !!type && type.symmetry !== "symmetric" && !edge.mutual;
  const canBeMutual = type?.symmetry === "either";

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Relationship</DialogTitle>
          <DialogDescription>
            {editable
              ? `Between these two ${nodeNoun}s. Changes save as you make them.`
              : `Between these two ${nodeNoun}s. Turn on Edit to change it.`}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-1.5 text-sm">
            <ColorDot color={type?.color ?? null} />
            <span className="font-medium">{edge.type_name}</span>
            {edge.mutual && (
              <Badge variant="outline" className="text-[10px]">
                mutual
              </Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground">
            {sourceName} is the {edge.source_label}. {targetName} is the{" "}
            {edge.target_label}.
          </p>

          {row ? (
            <RelationshipPrivacyControl
              scope={isMember ? "member" : "group"}
              edge={row}
              layout="stacked"
              label="Visibility"
              readOnly={!editable}
            />
          ) : (
            <p className="text-xs text-muted-foreground">
              {rows ? "This relationship is gone." : "Loading visibility..."}
            </p>
          )}

          {editable && canBeMutual && type && (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={edge.mutual}
                disabled={update.isPending}
                onCheckedChange={(v) =>
                  update.mutate({
                    edgeId: edge.id,
                    data: { mutual: v === true },
                  })
                }
              />
              Mutual (both are {type.forward_label})
            </label>
          )}

          {editable && canFlip && (
            <div className="space-y-1">
              <Button
                variant="outline"
                size="sm"
                disabled={update.isPending}
                onClick={() =>
                  update.mutate({ edgeId: edge.id, data: { flip: true } })
                }
              >
                Reverse direction
              </Button>
              <p className="text-[11px] text-muted-foreground">
                Makes {targetName} the {edge.source_label} and {sourceName} the{" "}
                {edge.target_label}.
              </p>
            </div>
          )}
        </div>
        <DialogFooter>
          {editable ? (
            <Button
              variant="destructive"
              size="sm"
              disabled={remove.isPending}
              onClick={() => remove.mutate(edge.id, { onSuccess: onClose })}
            >
              {remove.isPending ? "Removing..." : "Remove relationship"}
            </Button>
          ) : (
            <Button variant="outline" size="sm" onClick={onClose}>
              Close
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The little form shown once two nodes are picked in "add relationship" mode.
 *  Mirrors the direction/mutual logic of the per-node editor, but source and
 *  target are the two explicitly-picked nodes. */
function AddEdgeDialog({
  scope,
  source,
  target,
  onClose,
}: {
  scope: "members" | "groups";
  source: { id: string; name: string };
  target: { id: string; name: string };
  onClose: () => void;
}) {
  const { data: types } = useRelationshipTypes();
  const createMember = useCreateMemberRelationship();
  const createGroup = useCreateGroupRelationship();
  const create = scope === "members" ? createMember : createGroup;

  const [typeId, setTypeId] = useState("");
  const [role, setRole] = useState<"forward" | "reverse">("forward");
  const [mutual, setMutual] = useState(false);
  // Private until said otherwise, same as the per-member editor.
  const [visibility, setVisibility] = useState<PrivacyLevel>("private");
  const [showNewType, setShowNewType] = useState(false);

  const type = types?.find((t) => t.id === typeId);
  const symmetry = type?.symmetry;
  const showRole = symmetry === "directional" || symmetry === "either";
  const showMutual = symmetry === "either";
  const roleHidden = showMutual && mutual;

  function onTypeChange(v: string) {
    setTypeId(v);
    setRole("forward");
    setMutual(false);
  }

  function submit() {
    if (!type) return;
    let payload: RelationshipEdgeCreate;
    if (symmetry === "symmetric") {
      payload = { source_id: source.id, target_id: target.id, relationship_type_id: type.id };
    } else if (showMutual && mutual) {
      payload = { source_id: source.id, target_id: target.id, relationship_type_id: type.id, mutual: true };
    } else if (role === "forward") {
      payload = { source_id: source.id, target_id: target.id, relationship_type_id: type.id };
    } else {
      payload = { source_id: target.id, target_id: source.id, relationship_type_id: type.id };
    }
    create.mutate({ ...payload, visibility }, { onSuccess: onClose });
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add relationship</DialogTitle>
          <DialogDescription>
            Between {source.name} and {target.name}.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-xs">Type</Label>
            <Select value={typeId} onValueChange={onTypeChange}>
              <SelectTrigger>
                <SelectValue placeholder="Choose a type..." />
              </SelectTrigger>
              <SelectContent>
                {(types ?? []).map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => setShowNewType(true)}
            >
              New relationship type
            </Button>
          </div>
          {showRole && !roleHidden && type && (
            <div className="space-y-1">
              <Label className="text-xs">Direction</Label>
              <Select value={role} onValueChange={(v) => setRole(v as "forward" | "reverse")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="forward">
                    {source.name} is the {type.forward_label}
                  </SelectItem>
                  <SelectItem value="reverse">
                    {source.name} is the {type.reverse_label}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
          {showMutual && type && (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={mutual}
                onCheckedChange={(v) => setMutual(v === true)}
              />
              Mutual (both are {type.forward_label})
            </label>
          )}
          <div className="space-y-1">
            <Label className="text-xs">Visibility</Label>
            <Select
              value={visibility}
              onValueChange={(v) => setVisibility(v as PrivacyLevel)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {EDGE_VISIBILITY_LEVELS.map((l) => (
                  <SelectItem key={l.value} value={l.value}>
                    {l.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[11px] text-muted-foreground">
              {EDGE_VISIBILITY_HELP}
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button onClick={submit} disabled={!typeId || create.isPending}>
            {create.isPending ? "Adding..." : "Add"}
          </Button>
        </DialogFooter>
      </DialogContent>
      {showNewType && (
        <RelationshipTypeDialog
          onOpenChange={(open) => !open && setShowNewType(false)}
          onCreated={(created) => onTypeChange(created.id)}
        />
      )}
    </Dialog>
  );
}

export function RelationshipsPage() {
  const [scope, setScope] = useState<"members" | "groups">("members");
  // Off by default: this page is mostly something you look at, and a graph you
  // are dragging around is not a place to be one stray click from changing
  // who is whose anything.
  const [editMode, setEditMode] = useState(false);
  const { data: graph, isLoading } = useRelationshipGraph(scope);

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Relationships</h1>
          <p className="text-sm text-muted-foreground">
            Drag to pan, scroll to zoom, drag a node to nudge it. Click a line
            between two to see the relationship it draws. Turn on Edit to add,
            reverse, republish or remove them; existing types are edited in
            Settings.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant={editMode ? "default" : "outline"}
            size="sm"
            onClick={() => setEditMode((m) => !m)}
            aria-pressed={editMode}
          >
            {editMode ? "Editing" : "Edit"}
          </Button>
          <div className="flex gap-1 rounded-md border p-1">
            {(["members", "groups"] as const).map((s) => (
              <Button
                key={s}
                variant={scope === s ? "default" : "ghost"}
                size="sm"
                onClick={() => setScope(s)}
                className="capitalize"
              >
                {s}
              </Button>
            ))}
          </div>
        </div>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading...</p>
      ) : !graph || graph.edges.length === 0 ? (
        <div className="rounded-lg border p-8 text-center text-sm text-muted-foreground">
          No {scope === "members" ? "member" : "group"} relationships yet. Add
          some from a {scope === "members" ? "member" : "group"}'s editor, then
          they will map out here.
        </div>
      ) : (
        <GraphCanvas graph={graph} scope={scope} editMode={editMode} />
      )}
    </div>
  );
}
