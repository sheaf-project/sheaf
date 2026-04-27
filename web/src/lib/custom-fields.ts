import type {
  CustomField,
  CustomFieldCreate,
  CustomFieldUpdate,
  CustomFieldValue,
  CustomFieldValueSet,
  DeleteResult,
  DestructiveConfirm,
} from "@/types/api";
import { apiFetch } from "./api-client";

export function listFields() {
  return apiFetch<CustomField[]>("/v1/fields");
}

export function createField(data: CustomFieldCreate) {
  return apiFetch<CustomField>("/v1/fields", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateField(id: string, data: CustomFieldUpdate) {
  return apiFetch<CustomField>(`/v1/fields/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function deleteField(id: string, confirm?: DestructiveConfirm) {
  return apiFetch<DeleteResult>(`/v1/fields/${id}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

export function getMemberFieldValues(memberId: string) {
  return apiFetch<CustomFieldValue[]>(`/v1/members/${memberId}/fields`);
}

export function setMemberFieldValues(memberId: string, values: CustomFieldValueSet[]) {
  return apiFetch<CustomFieldValue[]>(`/v1/members/${memberId}/fields`, {
    method: "PUT",
    body: JSON.stringify(values),
  });
}
