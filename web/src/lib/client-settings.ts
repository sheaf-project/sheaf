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

/**
 * Merge a partial object into the "web" client settings. The server does
 * an atomic top-level key merge, so independent callers each writing
 * their own key (front prefs, dismissed announcements, onboarding state)
 * can't clobber one another the way concurrent full-blob PUTs would.
 */
export function patchWebSettings(
  partial: Record<string, unknown>,
): Promise<ClientSettingsEntry> {
  return apiFetch("/v1/settings/client/web", {
    method: "PATCH",
    body: JSON.stringify({ settings: partial }),
  });
}
