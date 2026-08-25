import { ArrowLeft, ArrowLeftRight, ArrowRight } from "lucide-react";
import { useState } from "react";

import {
  useRelationshipTypes,
  useMemberRelationships,
  useCreateMemberRelationship,
  useDeleteMemberRelationship,
  useGroupRelationships,
  useCreateGroupRelationship,
  useDeleteGroupRelationship,
  useUpdateMemberRelationship,
  useUpdateGroupRelationship,
} from "@/hooks/use-relationships";
import type {
  PrivacyLevel,
  RelationshipDirection,
  RelationshipEdgeCreate,
  RelationshipFromViewpoint,
  RelationshipType,
} from "@/types/api";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ColorDot } from "@/components/color-dot";
import { Label } from "@/components/ui/label";
import {
  RelationshipPrivacyControl,
  RelationshipVisibilityBadge,
} from "@/components/relationship-privacy-control";
import { RelationshipTypeDialog } from "@/components/relationship-type-dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  EDGE_VISIBILITY_HELP,
  EDGE_VISIBILITY_LEVELS,
} from "@/lib/relationship-privacy";

type Role = "forward" | "reverse";

export interface RelationshipNode {
  id: string;
  name: string;
  /** False to keep this node out of the add-picker while still resolving its
   *  name in existing edges (e.g. custom-front pseudo-members). Defaults true. */
  selectable?: boolean;
}

/**
 * At-a-glance direction glyph for an existing edge: a two-way arrow for a
 * mutual / symmetric relationship, a one-way arrow otherwise (pointing away
 * from this node when it is the source, toward it when it is the target).
 */
function DirectionIcon({ direction }: { direction: RelationshipDirection }) {
  const cls = "h-3.5 w-3.5 shrink-0 text-muted-foreground";
  if (direction === "outgoing") {
    return <ArrowRight className={cls} aria-label="one-way, from this one" />;
  }
  if (direction === "incoming") {
    return <ArrowLeft className={cls} aria-label="one-way, toward this one" />;
  }
  return <ArrowLeftRight className={cls} aria-label="mutual" />;
}

/**
 * Shared relationship (edge) sub-editor for a single member or group. Lists
 * the node's existing relationships and offers an add form whose direction
 * controls adapt to the selected type's symmetry.
 */
export function RelationshipsEditor({
  nodeId,
  scope,
  nodes,
  readOnly = false,
}: {
  nodeId: string;
  scope: "member" | "group";
  nodes: RelationshipNode[];
  /** Display-only (member/group profile view): show the list, no add form or
   *  remove buttons, and render nothing when there are no relationships. */
  readOnly?: boolean;
}) {
  const isMember = scope === "member";
  const noun = isMember ? "member" : "group";

  const { data: types } = useRelationshipTypes();

  // Both scope hooks are always called (rules-of-hooks); the inactive one is
  // disabled via a null id and returns nothing.
  const memberEdges = useMemberRelationships(isMember ? nodeId : null);
  const groupEdges = useGroupRelationships(isMember ? null : nodeId);
  const edges = (isMember ? memberEdges.data : groupEdges.data) ?? [];

  const createMemberEdge = useCreateMemberRelationship();
  const createGroupEdge = useCreateGroupRelationship();
  const deleteMemberEdge = useDeleteMemberRelationship();
  const deleteGroupEdge = useDeleteGroupRelationship();
  const updateMemberEdge = useUpdateMemberRelationship();
  const updateGroupEdge = useUpdateGroupRelationship();
  const createEdge = isMember ? createMemberEdge : createGroupEdge;
  const deleteEdge = isMember ? deleteMemberEdge : deleteGroupEdge;
  const updateEdge = isMember ? updateMemberEdge : updateGroupEdge;

  // The full list resolves names in existing edges; the picker excludes
  // self and non-selectable nodes (e.g. custom fronts).
  const others = nodes.filter((n) => n.id !== nodeId && n.selectable !== false);
  const nodeName = (id: string) =>
    nodes.find((n) => n.id === id)?.name ?? id.slice(0, 8);

  const [typeId, setTypeId] = useState("");
  const [otherId, setOtherId] = useState("");
  const [role, setRole] = useState<Role>("forward");
  const [mutual, setMutual] = useState(false);
  // An edge says something about two people at once, so a new one starts
  // private no matter what either of them is set to.
  const [visibility, setVisibility] = useState<PrivacyLevel>("private");
  const [showNewType, setShowNewType] = useState(false);

  const selectedType: RelationshipType | undefined = types?.find(
    (t) => t.id === typeId,
  );
  const typeColor = (id: string) =>
    types?.find((t) => t.id === id)?.color ?? null;
  const symmetry = selectedType?.symmetry;
  const showRole = symmetry === "directional" || symmetry === "either";
  const showMutual = symmetry === "either";
  const roleHidden = showMutual && mutual;

  function onTypeChange(v: string) {
    setTypeId(v);
    // Re-baseline the direction controls when the type (and thus its
    // symmetry) changes.
    setRole("forward");
    setMutual(false);
  }

  function reset() {
    setTypeId("");
    setOtherId("");
    setRole("forward");
    setMutual(false);
    setVisibility("private");
  }

  function handleAdd() {
    if (!selectedType || !otherId) return;

    let payload: RelationshipEdgeCreate;
    if (symmetry === "symmetric") {
      // Order is irrelevant; the backend canonicalises.
      payload = {
        source_id: nodeId,
        target_id: otherId,
        relationship_type_id: selectedType.id,
      };
    } else if (showMutual && mutual) {
      // Mutual "either" edge: both ends read the forward label.
      payload = {
        source_id: nodeId,
        target_id: otherId,
        relationship_type_id: selectedType.id,
        mutual: true,
      };
    } else if (role === "forward") {
      // This node is the forward-label endpoint, i.e. the source.
      payload = {
        source_id: nodeId,
        target_id: otherId,
        relationship_type_id: selectedType.id,
      };
    } else {
      // This node is the reverse-label endpoint, i.e. the target.
      payload = {
        source_id: otherId,
        target_id: nodeId,
        relationship_type_id: selectedType.id,
      };
    }

    createEdge.mutate({ ...payload, visibility }, { onSuccess: reset });
  }

  // On a profile (read-only) with no relationships, render nothing.
  if (readOnly && edges.length === 0) return null;

  return (
    <div className="space-y-3 border-t pt-3">
      <p className="text-sm font-medium text-muted-foreground">Relationships</p>

      {edges.length > 0 ? (
        <div className="space-y-1">
          {edges.map((edge) => (
            <EdgeRow
              key={edge.id}
              edge={edge}
              scope={scope}
              color={typeColor(edge.relationship_type_id)}
              otherName={nodeName(edge.other_id)}
              readOnly={readOnly}
              onFlip={() =>
                updateEdge.mutate({ edgeId: edge.id, data: { flip: true } })
              }
              flipping={updateEdge.isPending}
              onRemove={() => deleteEdge.mutate(edge.id)}
              removing={deleteEdge.isPending}
            />
          ))}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">No relationships yet.</p>
      )}

      {readOnly ? null : !types || types.length === 0 ? (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            No relationship types yet. A type is the vocabulary (partner,
            parent/child, protector) an actual relationship is drawn with.
          </p>
          <Button size="sm" variant="outline" onClick={() => setShowNewType(true)}>
            New relationship type
          </Button>
        </div>
      ) : others.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No other {noun}s to link to yet.
        </p>
      ) : (
        <div className="space-y-2 rounded-md border p-2">
          <div className="space-y-1">
            <Label className="text-xs">Type</Label>
            <Select value={typeId} onValueChange={onTypeChange}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Choose a type..." />
              </SelectTrigger>
              <SelectContent>
                {types.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1">
            <Label className="text-xs">Other {noun}</Label>
            <Select value={otherId} onValueChange={setOtherId}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder={`Choose a ${noun}...`} />
              </SelectTrigger>
              <SelectContent>
                {others.map((n) => (
                  <SelectItem key={n.id} value={n.id}>
                    {n.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {showRole && !roleHidden && selectedType && (
            <div className="space-y-1">
              <Label className="text-xs">Direction</Label>
              <Select value={role} onValueChange={(v) => setRole(v as Role)}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="forward">
                    This {noun} is the {selectedType.forward_label}
                  </SelectItem>
                  <SelectItem value="reverse">
                    This {noun} is the {selectedType.reverse_label}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}

          {showMutual && selectedType && (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={mutual}
                onCheckedChange={(v) => setMutual(v === true)}
              />
              Mutual (both are {selectedType.forward_label})
            </label>
          )}

          <div className="space-y-1">
            <Label className="text-xs">Visibility</Label>
            <Select
              value={visibility}
              onValueChange={(v) => setVisibility(v as PrivacyLevel)}
            >
              <SelectTrigger className="w-full">
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

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              onClick={handleAdd}
              disabled={!typeId || !otherId || createEdge.isPending}
            >
              {createEdge.isPending ? "Adding..." : "Add relationship"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="text-xs"
              onClick={() => setShowNewType(true)}
            >
              New relationship type
            </Button>
          </div>
        </div>
      )}

      {showNewType && (
        <RelationshipTypeDialog
          onOpenChange={(open) => !open && setShowNewType(false)}
          onCreated={(created) => onTypeChange(created.id)}
        />
      )}
    </div>
  );
}

/**
 * One existing edge: what it is, and (unless this is a profile view) the
 * controls for it. The privacy select, its step-up prompt and the staged-raise
 * note all come from the shared control, so this row and the graph's edge
 * dialog cannot drift apart about the same setting.
 *
 * The badge shows in both modes, including a read-only profile: somebody
 * looking at a member should be able to see that a relationship is public
 * without having to open the editor to find out.
 */
function EdgeRow({
  edge,
  scope,
  color,
  otherName,
  readOnly,
  onFlip,
  flipping,
  onRemove,
  removing,
}: {
  edge: RelationshipFromViewpoint;
  scope: "member" | "group";
  color: string | null;
  otherName: string;
  readOnly: boolean;
  onFlip: () => void;
  flipping: boolean;
  onRemove: () => void;
  removing: boolean;
}) {
  // Directionless edges (symmetric type, or a mutual either-edge) have
  // nothing to reverse - same rule as the graph's edge dialog.
  const canFlip = edge.direction !== "none";
  return (
    <RelationshipPrivacyControl
      scope={scope}
      edge={edge}
      readOnly={readOnly}
      className="rounded-md border px-2 py-1 text-sm"
      trailing={
        readOnly ? null : (
          <>
            {canFlip && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 text-xs"
                onClick={onFlip}
                disabled={flipping}
                title={`Reverse direction: ${otherName} becomes the ${edge.label}`}
              >
                Reverse
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-xs text-destructive hover:text-destructive"
              onClick={onRemove}
              disabled={removing}
            >
              Remove
            </Button>
          </>
        )
      }
    >
      <span className="flex min-w-0 items-center gap-1.5 truncate">
        <ColorDot color={color} />
        <DirectionIcon direction={edge.direction} />
        <span className="min-w-0 truncate">
          <span className="text-muted-foreground">{edge.label}:</span>{" "}
          {otherName}
        </span>
        <RelationshipVisibilityBadge visibility={edge.visibility} />
      </span>
    </RelationshipPrivacyControl>
  );
}
