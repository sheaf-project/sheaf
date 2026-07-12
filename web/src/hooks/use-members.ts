import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  isDeleteQueued,
  type DestructiveConfirm,
  type MemberCreate,
  type MemberUpdate,
} from "@/types/api";
import * as api from "@/lib/members";
import { useDateFormatters } from "@/hooks/use-date-formatters";

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
  const { formatDate } = useDateFormatters();
  return useMutation({
    mutationFn: ({
      id,
      confirm,
    }: {
      id: string;
      confirm?: DestructiveConfirm;
    }) => api.deleteMember(id, confirm),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: memberKeys.all });
      if (isDeleteQueued(result)) {
        qc.invalidateQueries({ queryKey: ["system-safety"] });
        toast.success(
          `Member scheduled for deletion - cancellable in Settings until ${formatDate(result.finalize_after)}.`,
        );
      } else {
        toast.success("Member deleted");
      }
    },
  });
}

export function useArchiveMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      confirm,
    }: {
      id: string;
      confirm?: DestructiveConfirm;
    }) => api.archiveMember(id, confirm),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: memberKeys.all });
      qc.invalidateQueries({ queryKey: ["members", "top-fronters"] });
      qc.invalidateQueries({ queryKey: memberKeys.detail(id) });
      toast.success("Member archived");
    },
  });
}

export function useUnarchiveMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.unarchiveMember(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: memberKeys.all });
      qc.invalidateQueries({ queryKey: ["members", "top-fronters"] });
      qc.invalidateQueries({ queryKey: memberKeys.detail(id) });
      toast.success("Member unarchived");
    },
  });
}
