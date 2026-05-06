import { apiFetch } from "./api-client";
import type {
  DeleteResult,
  DestructiveConfirm,
  Reminder,
  ReminderCreate,
  ReminderUpdate,
} from "@/types/api";

export async function listReminders(): Promise<Reminder[]> {
  return apiFetch<Reminder[]>("/v1/reminders");
}

export async function createReminder(body: ReminderCreate): Promise<Reminder> {
  return apiFetch<Reminder>("/v1/reminders", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updateReminder(
  id: string,
  body: ReminderUpdate,
): Promise<Reminder> {
  return apiFetch<Reminder>(`/v1/reminders/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function deleteReminder(
  id: string,
  confirm?: DestructiveConfirm,
): Promise<DeleteResult> {
  return apiFetch<DeleteResult>(`/v1/reminders/${id}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

export async function getNextFire(
  id: string,
): Promise<{ next_fire_at: string | null }> {
  return apiFetch<{ next_fire_at: string | null }>(
    `/v1/reminders/${id}/next-fire`,
  );
}

// Day-of-week bit values matching Python's weekday() ordering (Mon=0..Sun=6).
export const DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
export const DOW_BITS = [1, 2, 4, 8, 16, 32, 64] as const;
