import type {
  DeleteResult,
  DestructiveConfirm,
  Member,
  Tag,
  TagCreate,
  TagUpdate,
} from "@/types/api";
import { apiFetch } from "./api-client";

export function listTags() {
  return apiFetch<Tag[]>("/v1/tags");
}

export function createTag(data: TagCreate) {
  return apiFetch<Tag>("/v1/tags", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateTag(id: string, data: TagUpdate) {
  return apiFetch<Tag>(`/v1/tags/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function deleteTag(id: string, confirm?: DestructiveConfirm) {
  return apiFetch<DeleteResult>(`/v1/tags/${id}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

export function getTagMembers(id: string) {
  return apiFetch<Member[]>(`/v1/tags/${id}/members`);
}

export function setTagMembers(id: string, memberIds: string[]) {
  return apiFetch<Member[]>(`/v1/tags/${id}/members`, {
    method: "PUT",
    body: JSON.stringify({ member_ids: memberIds }),
  });
}
