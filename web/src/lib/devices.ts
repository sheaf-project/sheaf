import { apiFetch } from "./api-client";

export type PushPlatform = "fcm" | "apns_dev" | "apns_prod";

export interface PushDevice {
  id: string;
  platform: PushPlatform;
  label: string | null;
  enabled: boolean;
  install_id: string | null;
  app_version: string | null;
  last_seen_at: string;
  created_at: string;
}

export interface PushDeviceUpdate {
  enabled?: boolean;
  label?: string | null;
}

export function listPushDevices() {
  return apiFetch<PushDevice[]>("/v1/devices/push");
}

export function updatePushDevice(id: string, body: PushDeviceUpdate) {
  return apiFetch<PushDevice>(`/v1/devices/push/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deletePushDevice(id: string) {
  return apiFetch<void>(`/v1/devices/push/${id}`, { method: "DELETE" });
}
