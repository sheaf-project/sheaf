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
  account_status: string;
  email_verified: boolean;
  signup_ip: string | null;
  member_limit: number | null;
  storage_used_bytes: number;
  member_count: number;
  created_at: string;
  last_login_at: string | null;
}

export interface PendingUser {
  id: string;
  email: string;
  email_verified: boolean;
  signup_ip: string | null;
  created_at: string;
}

export interface AdminUserPatch {
  tier?: string;
  is_admin?: boolean;
  member_limit?: number | null;
}

export interface AdminAuthStatus {
  level: "none" | "password" | "totp";
  verified: boolean;
  totp_enabled: boolean;
}

export function getAdminAuthStatus() {
  return apiFetch<AdminAuthStatus>("/v1/admin/auth");
}

export function verifyAdminStepUp(body: { password?: string; totp_code?: string }) {
  return apiFetch<{ verified: boolean }>("/v1/admin/auth", {
    method: "POST",
    body: JSON.stringify(body),
  });
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
  return apiFetch<{ pruned: number }>("/v1/admin/retention/run", { method: "POST" });
}

export function runCleanup() {
  return apiFetch<{ users_checked: number; total_orphaned: number; total_freed_bytes: number }>("/v1/admin/cleanup/run", { method: "POST" });
}

export function getStorageStats() {
  return apiFetch<{ total_bytes: number; total_files: number; users_with_files: number }>("/v1/admin/storage/stats");
}

export function getPendingApprovals() {
  return apiFetch<PendingUser[]>("/v1/admin/approvals");
}

export function approveUser(id: string) {
  return apiFetch<{ approved: boolean }>(`/v1/admin/users/${id}/approve`, { method: "POST" });
}

export function rejectUser(id: string) {
  return apiFetch<{ rejected: boolean }>(`/v1/admin/users/${id}/reject`, { method: "POST" });
}

// Invite codes

export interface InviteCode {
  id: string;
  code: string;
  created_by_email: string | null;
  max_uses: number;
  use_count: number;
  note: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface InviteCodeCreate {
  max_uses?: number;
  note?: string;
  expires_at?: string;
}

export function getInvites() {
  return apiFetch<InviteCode[]>("/v1/admin/invites");
}

export function createInvite(body: InviteCodeCreate) {
  return apiFetch<InviteCode>("/v1/admin/invites", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteInvite(id: string) {
  return apiFetch<{ deleted: boolean }>(`/v1/admin/invites/${id}`, {
    method: "DELETE",
  });
}
