import type { Member, MemberCreate, MemberUpdate } from "@/types/api";
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

export function deleteMember(
  id: string,
  confirm?: { password?: string; totp_code?: string },
) {
  return apiFetch<void>(`/v1/members/${id}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}
