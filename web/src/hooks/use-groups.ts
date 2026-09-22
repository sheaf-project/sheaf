import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  isDeleteQueued,
  type DestructiveConfirm,
  type GroupCreate,
  type GroupUpdate,
} from "@/types/api";
import * as api from "@/lib/groups";
import { useDateFormatters } from "@/hooks/use-date-formatters";

export const groupKeys = {
  all: ["groups"] as const,
  detail: (id: string) => ["groups", id] as const,
  members: (id: string) => ["groups", id, "members"] as const,
  /** The whole group list with member ids attached, in one request. */
  membership: ["groups", "membership"] as const,
};

// Membership changes rarely and this list backs a picker, not a live view:
// a stale map for a few minutes costs nothing, a refetch of every group's
// membership on every dialog open is what the fan-out this replaced used to
// be.
const MEMBERSHIP_STALE_MS = 5 * 60 * 1000;

export function useGroups() {
  return useQuery({
    queryKey: groupKeys.all,
    queryFn: () => api.listGroups(),
  });
}

export function useGroupMembers(id: string) {
  return useQuery({
    queryKey: groupKeys.members(id),
    queryFn: () => api.getGroupMembers(id),
  });
}

/**
 * Group id to the set of member ids in it, for every group, from ONE request.
 *
 * This used to be `useQueries` over every group, one `/members` call each:
 * on a system with a few hundred groups that was a few hundred concurrent
 * requests every time the member picker opened, which is exactly the burst
 * that made the API slow for everyone on 2026-09-22. The list endpoint can
 * carry the ids itself (`?include_member_ids=true`), so this asks for that
 * once and derives the map. The memo is keyed on the response object, which
 * react-query keeps stable between fetches, so the map is rebuilt only when
 * the data actually changes rather than on every render.
 */
export function useGroupMemberMap(): Map<string, Set<string>> {
  const { data: groups } = useQuery({
    queryKey: groupKeys.membership,
    queryFn: () => api.listGroups({ includeMemberIds: true }),
    staleTime: MEMBERSHIP_STALE_MS,
  });
  return useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const g of groups ?? []) map.set(g.id, new Set(g.member_ids ?? []));
    return map;
  }, [groups]);
}

export function useCreateGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      data,
      skipErrorToast = false,
    }: {
      data: GroupCreate;
      skipErrorToast?: boolean;
    }) => api.createGroup(data, skipErrorToast),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: groupKeys.all });
      toast.success("Group created");
    },
  });
}

export function useUpdateGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      data,
      skipErrorToast = false,
    }: {
      id: string;
      data: GroupUpdate;
      skipErrorToast?: boolean;
    }) => api.updateGroup(id, data, skipErrorToast),
    onSuccess: (group, { data }) => {
      qc.invalidateQueries({ queryKey: groupKeys.all });
      // A raise that would actually expose the group is staged, not applied,
      // so the toast must not claim the new level is live yet. Only a request
      // that touched privacy can be the one that staged it.
      toast.success(
        data.privacy && group.privacy_activates_at
          ? "Change confirmed - it goes live after your grace period."
          : "Group updated",
      );
    },
  });
}

export function useReorderGroups() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (groupIds: string[]) => api.reorderGroups(groupIds),
    // No success toast: each arrow click is a reorder, and a toast per
    // click would bury the list under confirmations of what the list
    // itself already shows.
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: groupKeys.all });
    },
  });
}

export function useDeleteGroup() {
  const qc = useQueryClient();
  const { formatDate } = useDateFormatters();
  return useMutation({
    mutationFn: ({
      id,
      confirm,
    }: {
      id: string;
      confirm?: DestructiveConfirm;
    }) => api.deleteGroup(id, confirm),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: groupKeys.all });
      if (isDeleteQueued(result)) {
        qc.invalidateQueries({ queryKey: ["system-safety"] });
        toast.success(
          `Group scheduled for deletion - cancellable in Settings until ${formatDate(result.finalize_after)}.`,
        );
      } else {
        toast.success("Group deleted");
      }
    },
  });
}

export function useSetGroupMembers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, memberIds }: { id: string; memberIds: string[] }) =>
      api.setGroupMembers(id, memberIds),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: groupKeys.members(id) });
      qc.invalidateQueries({ queryKey: groupKeys.membership });
      toast.success("Group members updated");
    },
  });
}
