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

/** A privacy raise that would actually expose the field is answered with a
 *  400 asking for step-up credentials, so callers pass `skipErrorToast` and
 *  re-prompt instead of firing a toast at something the user can still
 *  complete. */
export function updateField(
  id: string,
  data: CustomFieldUpdate,
  skipErrorToast = false,
) {
  return apiFetch<CustomField>(`/v1/fields/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
    skipErrorToast,
  });
}

/** Set each named field's sort order to its position in the list; fields
 *  not named keep theirs. Returns the full re-sorted list. */
export function reorderFields(fieldIds: string[]) {
  return apiFetch<CustomField[]>("/v1/fields/reorder", {
    method: "PUT",
    body: JSON.stringify({ field_ids: fieldIds }),
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
