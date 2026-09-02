import type { QueryClient } from "@tanstack/react-query";
import type { PendingActionType } from "@/types/api";
import { memberKeys } from "@/hooks/use-members";
import { groupKeys } from "@/hooks/use-groups";
import { tagKeys } from "@/hooks/use-tags";
import { fieldKeys } from "@/hooks/use-custom-fields";
import { frontKeys } from "@/hooks/use-fronts";
import { relationshipKeys } from "@/hooks/use-relationships";

/**
 * Which cached queries a pending action touches, keyed by its action type.
 *
 * A queued delete is what puts the "scheduled for deletion" badge (and the
 * disabled Delete button) on an entity's own list and detail views, so
 * queueing OR cancelling one has to invalidate those alongside
 * ["system-safety"]. Cancelling used to refresh only the Safety page, which
 * left the badge sitting on the members / groups / fields / tags / types
 * lists until something else happened to refetch them.
 *
 * Keys are prefixes: react-query matches by prefix, so ["members"] also
 * covers ["members", id]. The Record type is deliberate - adding a new
 * PendingActionType without deciding what it invalidates is a type error,
 * not a badge that quietly goes stale.
 */
export const pendingActionQueryKeys: Record<
  PendingActionType,
  readonly (readonly unknown[])[]
> = {
  member_delete: [memberKeys.all],
  group_delete: [groupKeys.all],
  tag_delete: [tagKeys.all],
  field_delete: [fieldKeys.all],
  front_delete: [frontKeys.all],
  journal_delete: [["journals"], ["journal"]],
  image_delete: [["files"]],
  // The target is a revision id, and revision histories hang off whichever
  // member / journal entry / message owns them, so there is no narrower key
  // to reach for than those three roots.
  revision_unpin: [["member"], ["members"], ["journal"], ["journals"], ["messages"]],
  watch_token_revoke: [["watch-tokens"], ["channels"]],
  channel_delete: [["channels"], ["channel"]],
  reminder_delete: [["reminders"]],
  poll_delete: [["polls"], ["poll"]],
  message_delete: [["messages"]],
  message_thread_delete: [["messages"]],
  relationship_type_delete: [relationshipKeys.types, ["relationships"]],
};

/**
 * Refresh everything a queued action's finalize / cancel changes: the Safety
 * page itself plus the entity surfaces carrying its pending badge.
 */
export function invalidateForPendingAction(
  qc: QueryClient,
  actionType: PendingActionType,
): void {
  qc.invalidateQueries({ queryKey: ["system-safety"] });
  for (const key of pendingActionQueryKeys[actionType] ?? []) {
    qc.invalidateQueries({ queryKey: [...key] });
  }
}
