import { apiFetch } from "./api-client";

export interface AdminStats {
  total_users: number;
  total_members: number;
  total_storage_bytes: number;
  users_by_tier: Record<string, number>;
}

export interface AdminUser {
  id: string;
  email: string;
  tier: string;
  is_admin: boolean;
  member_limit: number | null;
  storage_used_bytes: number;
  member_count: number;
  created_at: string;
  last_login_at: string | null;
}

export interface AdminUserPatch {
  tier?: string;
  is_admin?: boolean;
  member_limit?: number | null;
}

export function getAdminStats() {
  return apiFetch<AdminStats>("/v1/admin/stats");
}

export function getAdminUsers(search?: string, page = 1, limit = 50) {
  const params = new URLSearchParams({ page: String(page), limit: String(limit) });
  if (search) params.set("search", search);
  return apiFetch<AdminUser[]>(`/v1/admin/users?${params}`);
}

export function updateAdminUser(id: string, patch: AdminUserPatch) {
  return apiFetch<AdminUser>(`/v1/admin/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function runRetention() {
  return apiFetch<{ message: string; deleted: number }>("/v1/admin/retention/run", { method: "POST" });
}

export function runCleanup() {
  return apiFetch<{ message: string; deleted: number; freed_bytes: number }>("/v1/admin/cleanup/run", { method: "POST" });
}

export function runStorageAudit() {
  return apiFetch<{ users_checked: number; users_corrected: number }>("/v1/admin/storage/audit", { method: "POST" });
}
