import type {
  DeleteResult,
  DestructiveConfirm,
  Front,
  FrontAuditEvent,
  FrontCreate,
  FrontUpdate,
} from "@/types/api";
import { apiFetch, apiFetchWithHeaders } from "./api-client";

export interface FrontsPage {
  items: Front[];
  hasMore: boolean;
  nextCursor: string | null;
}

export interface FrontsPagedPage {
  items: Front[];
  hasMore: boolean;
  total: number;
}

/**
 * Fetch one page of the front history. Cursor pagination: omit `cursor`
 * for the first page, then pass the previous response's `nextCursor` to
 * advance. The server signals end-of-list with `hasMore = false`.
 */
export async function listFronts(
  opts: { limit?: number; cursor?: string | null } = {},
): Promise<FrontsPage> {
  const limit = opts.limit ?? 50;
  const params = new URLSearchParams({ limit: String(limit) });
  if (opts.cursor) params.set("cursor", opts.cursor);
  const { body, headers } = await apiFetchWithHeaders<Front[]>(
    `/v1/fronts?${params.toString()}`,
  );
  return {
    items: body,
    hasMore: headers.get("X-Sheaf-Has-More") === "true",
    nextCursor: headers.get("X-Sheaf-Next-Cursor"),
  };
}

/**
 * Fetch one numbered page (offset-based) with a total count for rendering
 * page-number navigation. Pays one extra `COUNT(*)` query on the server,
 * so use this for the paginated view; the cursor variant is cheaper for
 * "load more" / infinite-scroll flows.
 */
export async function listFrontsPaged(opts: {
  page: number;
  limit?: number;
}): Promise<FrontsPagedPage> {
  const limit = opts.limit ?? 50;
  const offset = Math.max(0, (opts.page - 1) * limit);
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
    include_total: "true",
  });
  const { body, headers } = await apiFetchWithHeaders<Front[]>(
    `/v1/fronts?${params.toString()}`,
  );
  return {
    items: body,
    hasMore: headers.get("X-Sheaf-Has-More") === "true",
    total: Number.parseInt(headers.get("X-Sheaf-Total-Count") ?? "0", 10),
  };
}

export function getCurrentFronts() {
  return apiFetch<Front[]>("/v1/fronts/current");
}

export function createFront(data: FrontCreate) {
  return apiFetch<Front>("/v1/fronts", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateFront(id: string, data: FrontUpdate) {
  return apiFetch<Front>(`/v1/fronts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function deleteFront(id: string, confirm?: DestructiveConfirm) {
  return apiFetch<DeleteResult>(`/v1/fronts/${id}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

export function listFrontAudit(id: string) {
  return apiFetch<FrontAuditEvent[]>(`/v1/fronts/${id}/audit`);
}
