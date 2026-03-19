import type { DeleteConfirmation, System, SystemUpdate } from "@/types/api";
import { apiFetch } from "./api-client";

export function getMySystem() {
  return apiFetch<System>("/v1/systems/me");
}

export function updateMySystem(data: SystemUpdate) {
  return apiFetch<System>("/v1/systems/me", {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function updateDeleteConfirmation(data: {
  level: DeleteConfirmation;
  password: string;
  totp_code?: string;
}) {
  return apiFetch<System>("/v1/systems/me/delete-confirmation", {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function exportData() {
  return apiFetch<Record<string, unknown>>("/v1/export");
}
