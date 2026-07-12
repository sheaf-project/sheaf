import { useMemo } from "react";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
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
};

export function useTags() {
  return useQuery({
    queryKey: tagKeys.all,
    queryFn: api.listTags,
  });
}

export function useTagMembers(id: string) {
  return useQuery({
    queryKey: tagKeys.members(id),
    queryFn: () => api.getTagMembers(id),
  });
}

export function useAllTagMembers(): Map<string, Set<string>> {
  const { data: tags } = useTags();
  const queries = useQueries({
    queries: (tags ?? []).map((t) => ({
      queryKey: tagKeys.members(t.id),
      queryFn: () => api.getTagMembers(t.id),
    })),
  });
  return useMemo(() => {
    const map = new Map<string, Set<string>>();
    (tags ?? []).forEach((t, i) => {
      const members = queries[i]?.data;
      if (members) map.set(t.id, new Set(members.map((m) => m.id)));
    });
    return map;
  }, [tags, queries]);
}

export function useSetTagMembers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, memberIds }: { id: string; memberIds: string[] }) =>
      api.setTagMembers(id, memberIds),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: tagKeys.members(vars.id) });
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
