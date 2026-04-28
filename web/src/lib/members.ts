import type {
  ContentRevision,
  DeleteResult,
  DestructiveConfirm,
  Member,
  MemberCreate,
  MemberUpdate,
} from "@/types/api";
import { apiFetch } from "./api-client";

export function listMembers() {
  return apiFetch<Member[]>("/v1/members");
}

export function getMember(id: string) {
  return apiFetch<Member>(`/v1/members/${id}`);
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

export function listMemberBioRevisions(id: string) {
  return apiFetch<ContentRevision[]>(`/v1/members/${id}/revisions`);
}

export function restoreMemberBioRevision(id: string, revisionId: string) {
  return apiFetch<Member>(`/v1/members/${id}/restore-revision`, {
    method: "POST",
    body: JSON.stringify({ revision_id: revisionId }),
  });
}
