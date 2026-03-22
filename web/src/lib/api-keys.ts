import type { ApiKey, ApiKeyCreated } from "@/types/api";
import { apiFetch } from "./api-client";

export function listApiKeys() {
  return apiFetch<ApiKey[]>("/v1/auth/keys");
}

export function createApiKey(data: { name: string; scopes: string[]; expires_at?: string | null }) {
  return apiFetch<ApiKeyCreated>("/v1/auth/keys", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function revokeApiKey(id: string) {
  return apiFetch<void>(`/v1/auth/keys/${id}`, { method: "DELETE" });
}
