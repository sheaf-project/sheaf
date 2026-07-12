import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";
import {
  isDeleteQueued,
  type DestructiveConfirm,
  type FrontCreate,
  type FrontUpdate,
} from "@/types/api";
import * as api from "@/lib/fronts";
import { useDateFormatters } from "@/hooks/use-date-formatters";

export const frontKeys = {
  all: ["fronts"] as const,
  current: ["fronts", "current"] as const,
};

/**
 * Cursor-paginated front history. Surface `fetchNextPage` + `hasNextPage`
 * to wire a "Load older" button; the `items` getter flattens the page
 * array so consumers don't have to know about page boundaries.
 */
export function useFronts(limit = 50) {
  const query = useInfiniteQuery({
    queryKey: [...frontKeys.all, "history", limit],
    queryFn: ({ pageParam }: { pageParam: string | null }) =>
      api.listFronts({ limit, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) =>
      lastPage.hasMore ? lastPage.nextCursor : null,
  });
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  return { ...query, items };
}

/**
 * Offset-paginated front history with a known total count, for the
 * numbered-pages view. `placeholderData: keepPreviousData` keeps the
 * old page visible while the new one loads so the page doesn't flash
 * empty when paging.
 */
export function useFrontsPaged(page: number, limit = 50) {
  const safePage = Math.max(1, page);
  return useQuery({
    queryKey: [...frontKeys.all, "paged", safePage, limit],
    queryFn: () => api.listFrontsPaged({ page: safePage, limit }),
    placeholderData: (prev) => prev,
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
  const { formatDate } = useDateFormatters();
  return useMutation({
    mutationFn: ({
      id,
      confirm,
    }: {
      id: string;
      confirm?: DestructiveConfirm;
    }) => api.deleteFront(id, confirm),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: frontKeys.all });
      qc.invalidateQueries({ queryKey: frontKeys.current });
      if (isDeleteQueued(result)) {
        qc.invalidateQueries({ queryKey: ["system-safety"] });
        toast.success(
          `Front entry scheduled for deletion - cancellable in Settings until ${formatDate(result.finalize_after)}.`,
        );
      } else {
        toast.success("Front deleted");
      }
    },
  });
}
