import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { CustomFieldCreate, CustomFieldUpdate, CustomFieldValueSet } from "@/types/api";
import * as api from "@/lib/custom-fields";
import { memberKeys } from "./use-members";

export const fieldKeys = {
  all: ["custom-fields"] as const,
  memberValues: (memberId: string) => ["custom-fields", "member", memberId] as const,
};

export function useCustomFields() {
  return useQuery({
    queryKey: fieldKeys.all,
    queryFn: api.listFields,
  });
}

export function useCreateField() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CustomFieldCreate) => api.createField(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: fieldKeys.all }),
  });
}

export function useUpdateField() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: CustomFieldUpdate }) =>
      api.updateField(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: fieldKeys.all }),
  });
}

export function useDeleteField() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteField(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: fieldKeys.all }),
  });
}

export function useMemberFieldValues(memberId: string | null) {
  return useQuery({
    queryKey: fieldKeys.memberValues(memberId ?? ""),
    queryFn: () => api.getMemberFieldValues(memberId!),
    enabled: !!memberId,
  });
}

export function useSetMemberFieldValues() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ memberId, values }: { memberId: string; values: CustomFieldValueSet[] }) =>
      api.setMemberFieldValues(memberId, values),
    onSuccess: (_data, { memberId }) => {
      qc.invalidateQueries({ queryKey: fieldKeys.memberValues(memberId) });
      qc.invalidateQueries({ queryKey: memberKeys.all });
    },
  });
}
