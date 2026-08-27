import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import {
  useUpdateGroupRelationship,
  useUpdateMemberRelationship,
} from "@/hooks/use-relationships";
import { isStepUpRequiredError, showApiErrorToast } from "@/lib/api-errors";
import {
  EDGE_VISIBILITY_HELP,
  EDGE_VISIBILITY_LEVELS,
} from "@/lib/relationship-privacy";
import { getSystemSafety } from "@/lib/system-safety";
import { getMySystem } from "@/lib/systems";
import { cn } from "@/lib/utils";
import type {
  DeleteConfirmation,
  DestructiveConfirm,
  PrivacyLevel,
} from "@/types/api";

/** The privacy-bearing subset of an edge, common to every shape the API hands
 *  back (viewpoint rows, the raw edge). Anything carrying these can be handed
 *  to the control. */
export interface RelationshipEdgePrivacy {
  id: string;
  visibility: PrivacyLevel;
  pending_visibility: PrivacyLevel | null;
  visibility_activates_at: string | null;
}

/**
 * Privacy level of an edge, at a glance. Only earns chrome when it is not
 * private: private is the default and the safe state, so saying so on every
 * row would be noise that trains people to stop reading the badge that
 * matters.
 */
export function RelationshipVisibilityBadge({
  visibility,
  className,
}: {
  visibility: PrivacyLevel;
  className?: string;
}) {
  if (visibility === "private") return null;
  return (
    <Badge variant="outline" className={cn("shrink-0 text-[10px]", className)}>
      {visibility}
    </Badge>
  );
}

/**
 * The per-edge privacy setting, everywhere it is offered on an existing edge:
 * the three-level select, the step-up prompt a raise can come back asking for,
 * and the note saying a staged raise has not happened yet.
 *
 * One component so the surfaces that offer it (the relationship list on a
 * member or group, the edge dialog on the graph) cannot drift into behaving
 * differently about the same setting. Only the shell differs: `row` slots into
 * a dense list line, `stacked` into a form.
 */
export function RelationshipPrivacyControl({
  scope,
  edge,
  layout = "row",
  className,
  readOnly = false,
  showHelp = layout === "stacked",
  label,
  children,
  trailing,
}: {
  scope: "member" | "group";
  edge: RelationshipEdgePrivacy;
  /** `row`: select (and any `trailing` action) on the right of a list line,
   *  with `children` as the line's own content. `stacked`: labelled form
   *  field, full width. */
  layout?: "row" | "stacked";
  className?: string;
  /** Display only: no select, but a staged raise is still declared - somebody
   *  reading a profile should still see that it is about to change. */
  readOnly?: boolean;
  /** One-line explanation of what public does. On by default when stacked. */
  showHelp?: boolean;
  /** Field label, `stacked` only. */
  label?: string;
  /** `row` only: the left-hand content of the line. */
  children?: ReactNode;
  /** `row` only: extra controls beside the select (e.g. a remove button). */
  trailing?: ReactNode;
}) {
  const { formatDate } = useDateFormatters();
  const isMember = scope === "member";

  // Both scope hooks are always called (rules-of-hooks); only one is used.
  const updateMemberEdge = useUpdateMemberRelationship();
  const updateGroupEdge = useUpdateGroupRelationship();
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

  const [pendingRaise, setPendingRaise] = useState<{
    visibility: PrivacyLevel;
    tier: DeleteConfirmation;
  } | null>(null);

  /** Move this edge to another privacy level.
   *
   * Sent without credentials first: only a raise that would actually put the
   * edge in front of someone is answered with a 400 asking for them, so the
   * common case stays a single click and the prompt only appears when it is
   * genuinely a step-up. Lowering is never gated.
   */
  function changeVisibility(next: PrivacyLevel) {
    updateEdge.mutate(
      { edgeId: edge.id, data: { visibility: next }, skipErrorToast: true },
      {
        onError: (err) => {
          if (isStepUpRequiredError(err)) {
            setPendingRaise({
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

  const select = readOnly ? null : (
    <Select
      value={edge.visibility}
      onValueChange={(v) => changeVisibility(v as PrivacyLevel)}
      disabled={updateEdge.isPending}
    >
      <SelectTrigger
        className={layout === "row" ? "h-6 w-28 text-xs" : "w-full"}
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
  );

  // Display only, stacked: say what the level is in the same words the select
  // would have offered. A badge alone says nothing at all for `private`, which
  // in a dialog with no control in it is the one answer worth confirming. (A
  // row carries the badge in its own summary line, so it needs no second copy
  // over on the right.)
  const staticLevel = readOnly ? (
    <p className="text-sm">
      {EDGE_VISIBILITY_LEVELS.find((l) => l.value === edge.visibility)?.label ??
        edge.visibility}
    </p>
  ) : null;

  const help = showHelp ? (
    <p className="text-[11px] text-muted-foreground">{EDGE_VISIBILITY_HELP}</p>
  ) : null;

  // A staged raise has not happened yet, and the level shown above is still
  // the truth until it does. Say both.
  const pendingNote = edge.visibility_activates_at ? (
    <p className="text-[11px] text-amber-600 dark:text-amber-500">
      {edge.pending_visibility ?? "public"} - activates{" "}
      {formatDate(edge.visibility_activates_at)}. Until then this stays{" "}
      {edge.visibility}.
    </p>
  ) : null;

  const stepUp = (
    <DestructiveConfirmDialog
      open={!!pendingRaise}
      onOpenChange={(open) => !open && setPendingRaise(null)}
      title="Confirm public visibility change"
      description="Publishing this relationship can reveal it through an existing public profile or share link. Confirm now; if you have a grace period set, it takes effect after your System Safety window."
      tier={pendingRaise?.tier ?? "none"}
      actionLabel="Confirm change"
      actionLabelLoading="Saving..."
      loading={updateEdge.isPending}
      onConfirm={(confirm?: DestructiveConfirm) => {
        if (!pendingRaise) return;
        updateEdge.mutate(
          {
            edgeId: edge.id,
            data: { visibility: pendingRaise.visibility, ...confirm },
          },
          { onSuccess: () => setPendingRaise(null) },
        );
      }}
    />
  );

  if (layout === "stacked") {
    return (
      <div className={cn("space-y-1", className)}>
        {label && <Label className="text-xs">{label}</Label>}
        {select}
        {staticLevel}
        {help}
        {pendingNote}
        {stepUp}
      </div>
    );
  }

  return (
    <div className={cn("space-y-1", className)}>
      <div className="flex items-center justify-between gap-2">
        {children}
        <div className="flex shrink-0 items-center gap-1">
          {select}
          {trailing}
        </div>
      </div>
      {help}
      {pendingNote}
      {stepUp}
    </div>
  );
}
