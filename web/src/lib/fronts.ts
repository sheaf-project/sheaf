import type {
  DeleteResult,
  DestructiveConfirm,
  Front,
  FrontAuditEvent,
  FrontCreate,
  FrontUpdate,
} from "@/types/api";
import { apiFetch } from "./api-client";

export function listFronts(limit = 50, offset = 0) {
  return apiFetch<Front[]>(`/v1/fronts?limit=${limit}&offset=${offset}`);
}

export function getCurrentFronts() {
  return apiFetch<Front[]>("/v1/fronts/current");
}

export function createFront(data: FrontCreate) {
  return apiFetch<Front>("/v1/fronts", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateFront(id: string, data: FrontUpdate) {
  return apiFetch<Front>(`/v1/fronts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function deleteFront(id: string, confirm?: DestructiveConfirm) {
  return apiFetch<DeleteResult>(`/v1/fronts/${id}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

export function listFrontAudit(id: string) {
  return apiFetch<FrontAuditEvent[]>(`/v1/fronts/${id}/audit`);
}
