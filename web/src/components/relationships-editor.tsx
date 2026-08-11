import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowLeftRight, ArrowRight } from "lucide-react";
import { useState } from "react";

import {
  useRelationshipTypes,
  useMemberRelationships,
  useCreateMemberRelationship,
  useDeleteMemberRelationship,
  useUpdateMemberRelationship,
  useGroupRelationships,
  useCreateGroupRelationship,
  useDeleteGroupRelationship,
  useUpdateGroupRelationship,
} from "@/hooks/use-relationships";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { ApiError } from "@/lib/api-error";
import { showApiErrorToast } from "@/lib/api-errors";
import { getSystemSafety } from "@/lib/system-safety";
import { getMySystem } from "@/lib/systems";
import type {
  DeleteConfirmation,
  DestructiveConfirm,
  PrivacyLevel,
  RelationshipDirection,
  RelationshipEdgeCreate,
  RelationshipFromViewpoint,
  RelationshipType,
} from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ColorDot } from "@/components/color-dot";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { Label } from "@/components/ui/label";
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

  // Only read for the re-auth prompt below; both are cached queries the rest
  // of the app already keeps warm.
  const { data: safety } = useQuery({
    queryKey: ["system-safety"],
    queryFn: getSystemSafety,
  });
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });

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
  const [pendingRaise, setPendingRaise] = useState<{
    edgeId: string;
    visibility: PrivacyLevel;
    tier: DeleteConfirmation;
  } | null>(null);

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

  /** Move one existing edge to another privacy level.
   *
   * Sent without credentials first: only a raise that would actually put the
   * edge in front of someone is answered with a 400 asking for them, so the
   * common case stays a single click and the prompt only appears when it is
   * genuinely a step-up. Lowering is never gated.
   */
  function changeVisibility(edgeId: string, next: PrivacyLevel) {
    updateEdge.mutate(
      { edgeId, data: { visibility: next }, skipErrorToast: true },
      {
        onError: (err) => {
          if (
            err instanceof ApiError &&
            err.status === 400 &&
            (err.detail === "Password required" ||
              err.detail === "TOTP code required")
          ) {
            setPendingRaise({
              edgeId,
              visibility: next,
              tier:
                safety?.settings.auth_tier ??
                system?.delete_confirmation ??
                "password",
            });
            return;
          }
          showApiErrorToast(err, "Couldn't update this relationship.", {
            force: true,
          });
        },
      },
    );
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
              color={typeColor(edge.relationship_type_id)}
              otherName={nodeName(edge.other_id)}
              readOnly={readOnly}
              busy={updateEdge.isPending}
              onVisibilityChange={(v) => changeVisibility(edge.id, v)}
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

      <DestructiveConfirmDialog
        open={!!pendingRaise}
        onOpenChange={(open) => !open && setPendingRaise(null)}
        title="Confirm public visibility change"
        description="Publishing this relationship can reveal it through an existing public profile or share link. Confirm now; it takes effect after your System Safety grace period."
        tier={pendingRaise?.tier ?? "none"}
        actionLabel="Confirm change"
        actionLabelLoading="Saving..."
        loading={updateEdge.isPending}
        onConfirm={(confirm?: DestructiveConfirm) => {
          if (!pendingRaise) return;
          updateEdge.mutate(
            {
              edgeId: pendingRaise.edgeId,
              data: { visibility: pendingRaise.visibility, ...confirm },
            },
            { onSuccess: () => setPendingRaise(null) },
          );
        }}
      />
    </div>
  );
}

/**
 * One existing edge. The privacy level only earns chrome when it is not
 * private: private is the default and the safe state, so saying so on every
 * row would be noise that trains people to stop reading the badge that
 * matters.
 */
function EdgeRow({
  edge,
  color,
  otherName,
  readOnly,
  busy,
  onVisibilityChange,
  onRemove,
  removing,
}: {
  edge: RelationshipFromViewpoint;
  color: string | null;
  otherName: string;
  readOnly: boolean;
  busy: boolean;
  onVisibilityChange: (visibility: PrivacyLevel) => void;
  onRemove: () => void;
  removing: boolean;
}) {
  const { formatDate } = useDateFormatters();

  return (
    <div className="space-y-1 rounded-md border px-2 py-1 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5 truncate">
          <ColorDot color={color} />
          <DirectionIcon direction={edge.direction} />
          <span className="min-w-0 truncate">
            <span className="text-muted-foreground">{edge.label}:</span>{" "}
            {otherName}
          </span>
          {edge.visibility !== "private" && (
            <Badge variant="outline" className="shrink-0 text-[10px]">
              {edge.visibility}
            </Badge>
          )}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          {!readOnly && (
            <>
              <Select
                value={edge.visibility}
                onValueChange={(v) => onVisibilityChange(v as PrivacyLevel)}
                disabled={busy}
              >
                <SelectTrigger
                  className="h-6 w-28 text-xs"
                  aria-label="Visibility"
                >
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
          )}
        </div>
      </div>
      {edge.visibility_activates_at && (
        <p className="text-[11px] text-amber-600 dark:text-amber-500">
          {edge.pending_visibility ?? "public"} - activates{" "}
          {formatDate(edge.visibility_activates_at)}. Until then this stays{" "}
          {edge.visibility}.
        </p>
      )}
    </div>
  );
}
