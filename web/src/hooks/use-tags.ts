import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  isDeleteQueued,
  type DestructiveConfirm,
  type TagCreate,
  type TagUpdate,
} from "@/types/api";
import * as api from "@/lib/tags";
import { useDateFormatters } from "@/hooks/use-date-formatters";

export const tagKeys = {
  all: ["tags"] as const,
  members: (id: string) => ["tags", id, "members"] as const,
  /** The whole tag list with member ids attached, in one request. */
  membership: ["tags", "membership"] as const,
};

// See the matching constant in use-groups.ts.
const MEMBERSHIP_STALE_MS = 5 * 60 * 1000;

export function useTags() {
  return useQuery({
    queryKey: tagKeys.all,
    queryFn: () => api.listTags(),
  });
}

export function useTagMembers(id: string) {
  return useQuery({
    queryKey: tagKeys.members(id),
    queryFn: () => api.getTagMembers(id),
  });
}

/** Tag id to the set of member ids carrying it, from one request. Same
 *  shape and same reasoning as `useGroupMemberMap` in use-groups.ts. */
export function useTagMemberMap(): Map<string, Set<string>> {
  const { data: tags } = useQuery({
    queryKey: tagKeys.membership,
    queryFn: () => api.listTags({ includeMemberIds: true }),
    staleTime: MEMBERSHIP_STALE_MS,
  });
  return useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const t of tags ?? []) map.set(t.id, new Set(t.member_ids ?? []));
    return map;
  }, [tags]);
}

export function useSetTagMembers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, memberIds }: { id: string; memberIds: string[] }) =>
      api.setTagMembers(id, memberIds),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: tagKeys.members(vars.id) });
      qc.invalidateQueries({ queryKey: tagKeys.membership });
      // Member-side tag list is the symmetric view; invalidate broadly.
      qc.invalidateQueries({ queryKey: ["member"] });
    },
  });
}

export function useCreateTag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: TagCreate) => api.createTag(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: tagKeys.all });
      toast.success("Tag created");
    },
  });
}

export function useUpdateTag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: TagUpdate }) =>
      api.updateTag(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: tagKeys.all });
      toast.success("Tag updated");
    },
  });
}

export function useDeleteTag() {
  const qc = useQueryClient();
  const { formatDate } = useDateFormatters();
  return useMutation({
    mutationFn: ({
      id,
      confirm,
    }: {
      id: string;
      confirm?: DestructiveConfirm;
    }) => api.deleteTag(id, confirm),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: tagKeys.all });
      if (isDeleteQueued(result)) {
        qc.invalidateQueries({ queryKey: ["system-safety"] });
        toast.success(
          `Tag scheduled for deletion - cancellable in Settings until ${formatDate(result.finalize_after)}.`,
        );
      } else {
        toast.success("Tag deleted");
      }
    },
  });
}
