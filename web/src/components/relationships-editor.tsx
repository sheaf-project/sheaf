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
} from "@/hooks/use-relationships";
import type {
  RelationshipDirection,
  RelationshipEdgeCreate,
  RelationshipType,
} from "@/types/api";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Role = "forward" | "reverse";

export interface RelationshipNode {
  id: string;
  name: string;
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
}: {
  nodeId: string;
  scope: "member" | "group";
  nodes: RelationshipNode[];
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
  const createEdge = isMember ? createMemberEdge : createGroupEdge;
  const deleteEdge = isMember ? deleteMemberEdge : deleteGroupEdge;

  const others = nodes.filter((n) => n.id !== nodeId);
  const nodeName = (id: string) =>
    nodes.find((n) => n.id === id)?.name ?? id.slice(0, 8);

  const [typeId, setTypeId] = useState("");
  const [otherId, setOtherId] = useState("");
  const [role, setRole] = useState<Role>("forward");
  const [mutual, setMutual] = useState(false);

  const selectedType: RelationshipType | undefined = types?.find(
    (t) => t.id === typeId,
  );
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

    createEdge.mutate(payload, { onSuccess: reset });
  }

  return (
    <div className="space-y-3 border-t pt-3">
      <p className="text-sm font-medium text-muted-foreground">Relationships</p>

      {edges.length > 0 ? (
        <div className="space-y-1">
          {edges.map((edge) => (
            <div
              key={edge.id}
              className="flex items-center justify-between gap-2 rounded-md border px-2 py-1 text-sm"
            >
              <span className="flex min-w-0 items-center gap-1.5 truncate">
                <DirectionIcon direction={edge.direction} />
                <span className="min-w-0 truncate">
                  <span className="text-muted-foreground">{edge.label}:</span>{" "}
                  {nodeName(edge.other_id)}
                </span>
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 shrink-0 text-xs text-destructive hover:text-destructive"
                onClick={() => deleteEdge.mutate(edge.id)}
                disabled={deleteEdge.isPending}
              >
                Remove
              </Button>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">No relationships yet.</p>
      )}

      {!types || types.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          Define a relationship type in Settings &gt; Relationships first.
        </p>
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

          <Button
            size="sm"
            onClick={handleAdd}
            disabled={!typeId || !otherId || createEdge.isPending}
          >
            {createEdge.isPending ? "Adding..." : "Add relationship"}
          </Button>
        </div>
      )}
    </div>
  );
}
