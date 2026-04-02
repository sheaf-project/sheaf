import { apiFetch } from "./api-client";

export interface Announcement {
  id: string;
  title: string;
  body: string;
  severity: "info" | "warning" | "critical";
  dismissible: boolean;
  active: boolean;
  created_by: string | null;
  starts_at: string | null;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AnnouncementCreate {
  title: string;
  body: string;
  severity?: string;
  dismissible?: boolean;
  active?: boolean;
  starts_at?: string | null;
  expires_at?: string | null;
}

export interface AnnouncementUpdate {
  title?: string;
  body?: string;
  severity?: string;
  dismissible?: boolean;
  active?: boolean;
  starts_at?: string | null;
  expires_at?: string | null;
  clear_starts_at?: boolean;
  clear_expires_at?: boolean;
}

// Public — active announcements for the current user
export function getActiveAnnouncements() {
  return apiFetch<Announcement[]>("/v1/announcements");
}

// Admin — all announcements
export function getAdminAnnouncements() {
  return apiFetch<Announcement[]>("/v1/admin/announcements");
}

export function createAnnouncement(body: AnnouncementCreate) {
  return apiFetch<Announcement>("/v1/admin/announcements", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateAnnouncement(id: string, body: AnnouncementUpdate) {
  return apiFetch<Announcement>(`/v1/admin/announcements/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteAnnouncement(id: string) {
  return apiFetch<void>(`/v1/admin/announcements/${id}`, {
    method: "DELETE",
  });
}
