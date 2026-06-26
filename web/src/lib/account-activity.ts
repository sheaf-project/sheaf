import { apiFetch } from "./api-client";

export type AccountActivityActorType = "user" | "system";

export interface AccountActivityEvent {
  id: string;
  actor_type: AccountActivityActorType;
  action: string;
  target_label: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export function getMyAccountActivity(page = 1, limit = 50) {
  return apiFetch<AccountActivityEvent[]>(
    `/v1/account/activity?page=${page}&limit=${limit}`,
  );
}
