import type {
  DeleteResult,
  DestructiveConfirm,
  Group,
  GroupCreate,
  GroupUpdate,
  Member,
} from "@/types/api";
import { apiFetch } from "./api-client";

export function listGroups() {
  return apiFetch<Group[]>("/v1/groups");
}

export function getGroup(id: string) {
  return apiFetch<Group>(`/v1/groups/${id}`);
}

export function createGroup(data: GroupCreate) {
  return apiFetch<Group>("/v1/groups", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateGroup(id: string, data: GroupUpdate) {
  return apiFetch<Group>(`/v1/groups/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function deleteGroup(id: string, confirm?: DestructiveConfirm) {
  return apiFetch<DeleteResult>(`/v1/groups/${id}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

export function getGroupMembers(id: string) {
  return apiFetch<Member[]>(`/v1/groups/${id}/members`);
}

export function setGroupMembers(id: string, memberIds: string[]) {
  return apiFetch<Member[]>(`/v1/groups/${id}/members`, {
    method: "PUT",
    body: JSON.stringify({ member_ids: memberIds }),
  });
}
