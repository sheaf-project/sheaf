import type {
  SystemSafetyResponse,
  SystemSafetyUpdate,
  SystemSafetyUpdateResponse,
} from "@/types/api";
import { apiFetch } from "./api-client";

export function getSystemSafety() {
  return apiFetch<SystemSafetyResponse>("/v1/system/safety");
}

export function updateSystemSafety(data: SystemSafetyUpdate) {
  return apiFetch<SystemSafetyUpdateResponse>("/v1/system/safety", {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function cancelPendingAction(id: string) {
  return apiFetch<void>(`/v1/system/safety/pending-actions/${id}`, {
    method: "DELETE",
  });
}

export function cancelPendingChange(id: string) {
  return apiFetch<void>(`/v1/system/safety/pending-changes/${id}`, {
    method: "DELETE",
  });
}
