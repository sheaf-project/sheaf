import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { FrontCreate, FrontUpdate } from "@/types/api";
import * as api from "@/lib/fronts";

export const frontKeys = {
  all: ["fronts"] as const,
  current: ["fronts", "current"] as const,
};

export function useFronts(limit = 50, offset = 0) {
  return useQuery({
    queryKey: [...frontKeys.all, limit, offset],
    queryFn: () => api.listFronts(limit, offset),
  });
}

export function useCurrentFronts() {
  return useQuery({
    queryKey: frontKeys.current,
    queryFn: api.getCurrentFronts,
    refetchInterval: 30_000,
  });
}

export function useCreateFront() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: FrontCreate) => api.createFront(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: frontKeys.all });
      qc.invalidateQueries({ queryKey: frontKeys.current });
      toast.success("Front started");
    },
  });
}

export function useUpdateFront() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: FrontUpdate }) =>
      api.updateFront(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: frontKeys.all });
      qc.invalidateQueries({ queryKey: frontKeys.current });
      toast.success("Front updated");
    },
  });
}

export function useDeleteFront() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteFront(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: frontKeys.all });
      qc.invalidateQueries({ queryKey: frontKeys.current });
      toast.success("Front deleted");
    },
  });
}
