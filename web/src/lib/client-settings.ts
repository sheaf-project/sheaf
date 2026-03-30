import { apiFetch } from "./api-client";

export interface ClientSettingsEntry {
  client_id: string;
  settings: Record<string, unknown>;
}

export function listClientSettings(): Promise<ClientSettingsEntry[]> {
  return apiFetch("/v1/settings/client");
}

export function deleteClientSettings(clientId: string): Promise<void> {
  return apiFetch(`/v1/settings/client/${encodeURIComponent(clientId)}`, {
    method: "DELETE",
  });
}
