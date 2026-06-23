import type {
  ContentRevision,
  DeleteResult,
  DestructiveConfirm,
  Member,
  MemberCreate,
  MemberUpdate,
  Tag,
  UnpinRevisionResponse,
} from "@/types/api";
import { apiFetch } from "./api-client";

export function listMembers() {
  return apiFetch<Member[]>("/v1/members");
}

export function getMember(id: string) {
  return apiFetch<Member>(`/v1/members/${id}`);
}

/** Members ranked for a quick-switch list: pinned first, then by a
 *  recency-weighted fronting score. */
export function getTopFronters(limit = 8) {
  return apiFetch<Member[]>(`/v1/members/top-fronters?limit=${limit}`);
}

export interface MemberLimitStatus {
  /** 0 means unlimited. */
  limit: number;
  current: number;
  /** null when unlimited. */
  remaining: number | null;
}

/** Effective member cap + current usage, for warning before an import would
 *  exceed it. */
export function getMemberLimit() {
  return apiFetch<MemberLimitStatus>("/v1/members/limit");
}

export function createMember(data: MemberCreate) {
  return apiFetch<Member>("/v1/members", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateMember(id: string, data: MemberUpdate) {
  return apiFetch<Member>(`/v1/members/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function deleteMember(id: string, confirm?: DestructiveConfirm) {
  return apiFetch<DeleteResult>(`/v1/members/${id}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

/** Archive (soft-hide) a member. The optional confirm body is only needed
 *  when the System Safety "archive" category is on and an auth tier is
 *  configured. Returns the updated member. */
export function archiveMember(id: string, confirm?: DestructiveConfirm) {
  return apiFetch<Member>(`/v1/members/${id}/archive`, {
    method: "POST",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

/** Restore an archived member. Ungated; no body. */
export function unarchiveMember(id: string) {
  return apiFetch<Member>(`/v1/members/${id}/unarchive`, {
    method: "POST",
  });
}

export function listMemberBioRevisions(id: string) {
  return apiFetch<ContentRevision[]>(`/v1/members/${id}/revisions`);
}

export function restoreMemberBioRevision(id: string, revisionId: string) {
  return apiFetch<Member>(`/v1/members/${id}/restore-revision`, {
    method: "POST",
    body: JSON.stringify({ revision_id: revisionId }),
  });
}

export function pinMemberBioRevision(id: string, revisionId: string) {
  return apiFetch<ContentRevision>(`/v1/members/${id}/pin-revision`, {
    method: "POST",
    body: JSON.stringify({ revision_id: revisionId }),
  });
}

export function unpinMemberBioRevision(
  id: string,
  revisionId: string,
  confirm?: DestructiveConfirm,
) {
  return apiFetch<UnpinRevisionResponse>(`/v1/members/${id}/unpin-revision`, {
    method: "POST",
    body: JSON.stringify({ revision_id: revisionId, ...(confirm ?? {}) }),
  });
}

export function getMemberTags(id: string) {
  return apiFetch<Tag[]>(`/v1/members/${id}/tags`);
}

export function setMemberTags(id: string, tagIds: string[]) {
  return apiFetch<Tag[]>(`/v1/members/${id}/tags`, {
    method: "PUT",
    body: JSON.stringify({ tag_ids: tagIds }),
  });
}
