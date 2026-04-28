import type { RetentionSettings, RetentionUpdate } from "@/types/api";
import { apiFetch } from "./api-client";

export function getRetention() {
  return apiFetch<RetentionSettings>("/v1/retention");
}

export function updateRetention(data: RetentionUpdate) {
  return apiFetch<RetentionSettings>("/v1/retention", {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function cancelTrimNotice(id: string) {
  return apiFetch<void>(`/v1/retention/trim-notice/${id}`, {
    method: "DELETE",
  });
}
