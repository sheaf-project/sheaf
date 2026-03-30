import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { MemberCreate, MemberUpdate } from "@/types/api";
import * as api from "@/lib/members";

export const memberKeys = {
  all: ["members"] as const,
  detail: (id: string) => ["members", id] as const,
};

export function useMembers() {
  return useQuery({
    queryKey: memberKeys.all,
    queryFn: api.listMembers,
  });
}

export function useMember(id: string) {
  return useQuery({
    queryKey: memberKeys.detail(id),
    queryFn: () => api.getMember(id),
  });
}

export function useCreateMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: MemberCreate) => api.createMember(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: memberKeys.all });
      toast.success("Member created");
    },
  });
}

export function useUpdateMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: MemberUpdate }) =>
      api.updateMember(id, data),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: memberKeys.all });
      qc.invalidateQueries({ queryKey: memberKeys.detail(id) });
      toast.success("Member updated");
    },
  });
}

export function useDeleteMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      confirm,
    }: {
      id: string;
      confirm?: { password?: string; totp_code?: string };
    }) => api.deleteMember(id, confirm),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: memberKeys.all });
      toast.success("Member deleted");
    },
  });
}
