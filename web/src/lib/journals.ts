import type {
  ContentRevision,
  DeleteResult,
  DestructiveConfirm,
  JournalEntry,
  JournalEntryCreate,
  JournalEntryUpdate,
  JournalEntryWithCount,
  JournalListResponse,
  UnpinRevisionResponse,
} from "@/types/api";
import { apiFetch } from "./api-client";

export interface ListJournalsParams {
  member_id?: string | null;
  system_only?: boolean;
  before?: string | null;
  limit?: number;
}

export function listJournals(params: ListJournalsParams = {}) {
  const qs = new URLSearchParams();
  if (params.system_only) qs.set("system_only", "true");
  if (params.member_id) qs.set("member_id", params.member_id);
  if (params.before) qs.set("before", params.before);
  if (params.limit) qs.set("limit", String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<JournalListResponse>(`/v1/journals${suffix}`);
}

export function getJournal(id: string) {
  return apiFetch<JournalEntryWithCount>(`/v1/journals/${id}`);
}

export function createJournal(data: JournalEntryCreate) {
  return apiFetch<JournalEntry>("/v1/journals", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateJournal(id: string, data: JournalEntryUpdate) {
  return apiFetch<JournalEntry>(`/v1/journals/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function deleteJournal(id: string, confirm?: DestructiveConfirm) {
  return apiFetch<DeleteResult>(`/v1/journals/${id}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

export function listRevisions(id: string) {
  return apiFetch<ContentRevision[]>(`/v1/journals/${id}/revisions`);
}

export function restoreRevision(id: string, revisionId: string) {
  return apiFetch<JournalEntry>(`/v1/journals/${id}/restore-revision`, {
    method: "POST",
    body: JSON.stringify({ revision_id: revisionId }),
  });
}

export function pinJournalRevision(id: string, revisionId: string) {
  return apiFetch<ContentRevision>(`/v1/journals/${id}/pin-revision`, {
    method: "POST",
    body: JSON.stringify({ revision_id: revisionId }),
  });
}

export function unpinJournalRevision(
  id: string,
  revisionId: string,
  confirm?: DestructiveConfirm,
) {
  return apiFetch<UnpinRevisionResponse>(`/v1/journals/${id}/unpin-revision`, {
    method: "POST",
    body: JSON.stringify({ revision_id: revisionId, ...(confirm ?? {}) }),
  });
}
