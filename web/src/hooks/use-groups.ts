import { useMemo } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  isDeleteQueued,
  type DestructiveConfirm,
  type GroupCreate,
  type GroupUpdate,
} from "@/types/api";
import * as api from "@/lib/groups";

export const groupKeys = {
  all: ["groups"] as const,
  detail: (id: string) => ["groups", id] as const,
  members: (id: string) => ["groups", id, "members"] as const,
};

export function useGroups() {
  return useQuery({
    queryKey: groupKeys.all,
    queryFn: api.listGroups,
  });
}

export function useGroupMembers(id: string) {
  return useQuery({
    queryKey: groupKeys.members(id),
    queryFn: () => api.getGroupMembers(id),
  });
}

export function useAllGroupMembers(): Map<string, Set<string>> {
  const { data: groups } = useGroups();
  const queries = useQueries({
    queries: (groups ?? []).map((g) => ({
      queryKey: groupKeys.members(g.id),
      queryFn: () => api.getGroupMembers(g.id),
    })),
  });
  return useMemo(() => {
    const map = new Map<string, Set<string>>();
    (groups ?? []).forEach((g, i) => {
      const members = queries[i]?.data;
      if (members) map.set(g.id, new Set(members.map((m) => m.id)));
    });
    return map;
  }, [groups, queries]);
}

export function useCreateGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: GroupCreate) => api.createGroup(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: groupKeys.all });
      toast.success("Group created");
    },
  });
}

export function useUpdateGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: GroupUpdate }) =>
      api.updateGroup(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: groupKeys.all });
      toast.success("Group updated");
    },
  });
}

export function useDeleteGroup() {
  const qc = useQueryClient();
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
          `Group scheduled for deletion — cancellable in Settings until ${new Date(result.finalize_after).toLocaleDateString()}.`,
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
      toast.success("Group members updated");
    },
  });
}
