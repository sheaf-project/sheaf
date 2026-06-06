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
  totp_enabled: boolean;
  signup_ip: string | null;
  member_limit: number | null;
  storage_used_bytes: number;
  member_count: number;
  can_upload_images: boolean;
  can_upload_animated_images: boolean;
  created_at: string;
  last_login_at: string | null;
  suspended_until: string | null;
  suspended_reason: string | null;
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
  can_upload_images?: boolean;
  can_upload_animated_images?: boolean;
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

export function getAdminUsers(
  search?: string,
  page = 1,
  limit = 50,
  signupIp?: string,
) {
  const params = new URLSearchParams({ page: String(page), limit: String(limit) });
  if (search) params.set("search", search);
  if (signupIp) params.set("signup_ip", signupIp);
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

export interface PushoverUsage {
  month: string;
  count: number;
  cap: number;
  enforced: boolean;
}

export function getPushoverUsage() {
  return apiFetch<PushoverUsage>("/v1/admin/pushover-usage");
}

export interface AdminAuditEvent {
  id: string;
  admin_user_id: string | null;
  admin_email: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  target_user_id: string | null;
  reason: string | null;
  before_json: Record<string, unknown> | null;
  after_json: Record<string, unknown> | null;
  created_at: string;
}

export function getAdminAuditEvents(opts: {
  target_user_id?: string;
  admin_user_id?: string;
  action?: string;
  page?: number;
  limit?: number;
} = {}) {
  const params = new URLSearchParams();
  if (opts.target_user_id) params.set("target_user_id", opts.target_user_id);
  if (opts.admin_user_id) params.set("admin_user_id", opts.admin_user_id);
  if (opts.action) params.set("action", opts.action);
  params.set("page", String(opts.page ?? 1));
  params.set("limit", String(opts.limit ?? 50));
  return apiFetch<AdminAuditEvent[]>(`/v1/admin/audit-events?${params}`);
}

export interface UserAdminActivityEvent {
  id: string;
  admin_email: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  reason: string | null;
  before_json: Record<string, unknown> | null;
  after_json: Record<string, unknown> | null;
  created_at: string;
}

export function getMyAdminActivity(page = 1, limit = 50) {
  return apiFetch<UserAdminActivityEvent[]>(
    `/v1/auth/admin-activity?page=${page}&limit=${limit}`,
  );
}

// --- Emergency-support admin actions ---

export interface ResetSafetyResult {
  reset: boolean;
  changed_fields: string[];
}

export function adminResetSystemSafety(userId: string, reason: string) {
  return apiFetch<ResetSafetyResult>(
    `/v1/admin/users/${userId}/reset-safety`,
    { method: "POST", body: JSON.stringify({ reason }) },
  );
}

export interface BypassPendingResult {
  finalized_count: number;
  by_type: Record<string, number>;
}

export function adminBypassPendingActions(userId: string, reason: string) {
  return apiFetch<BypassPendingResult>(
    `/v1/admin/users/${userId}/bypass-pending`,
    { method: "POST", body: JSON.stringify({ reason }) },
  );
}

export interface AdminImportJobSummary {
  id: string;
  source: string;
  status: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  counts: Record<string, number>;
  last_error: string | null;
}

export interface AdminImportJobDetail extends AdminImportJobSummary {
  events: Array<{
    level: string;
    stage: string;
    message: string;
    record_ref: string | null;
  }>;
}

export function listUserImportJobs(userId: string) {
  return apiFetch<AdminImportJobSummary[]>(
    `/v1/admin/users/${userId}/import-jobs`,
  );
}

export function viewImportJobDetail(jobId: string, reason: string) {
  return apiFetch<AdminImportJobDetail>(`/v1/admin/import-jobs/${jobId}`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
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

// Scheduled jobs

export interface JobRunInfo {
  started_at: string;
  finished_at: string | null;
  status: string;
  items_processed: number;
  duration_ms: number | null;
  error_message: string | null;
  details: string | null;
}

export interface JobInfo {
  name: string;
  description: string;
  enabled: boolean;
  interval_seconds: number;
  last_run: JobRunInfo | null;
}

export interface JobLogEntry {
  id: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  items_processed: number;
  duration_ms: number | null;
  error_message: string | null;
  details: string | null;
}

export function getJobs() {
  return apiFetch<JobInfo[]>("/v1/admin/jobs");
}

export function triggerJob(jobName: string) {
  return apiFetch<JobRunInfo>(`/v1/admin/jobs/${jobName}/run`, {
    method: "POST",
  });
}

export function getJobLogs(jobName: string, limit = 20) {
  return apiFetch<JobLogEntry[]>(
    `/v1/admin/jobs/${jobName}/logs?limit=${limit}`,
  );
}

export function cancelUserDeletion(userId: string) {
  return apiFetch<{ cancelled: boolean }>(
    `/v1/admin/users/${userId}/cancel-deletion`,
    { method: "POST" },
  );
}

// Account recovery tools

export function resetUserPassword(userId: string, newPassword?: string) {
  return apiFetch<{ password: string; sessions_revoked: number }>(
    `/v1/admin/users/${userId}/reset-password`,
    {
      method: "POST",
      body: JSON.stringify({ new_password: newPassword ?? null }),
    },
  );
}

export function changeUserEmail(userId: string, newEmail: string) {
  return apiFetch<{ email: string }>(
    `/v1/admin/users/${userId}/change-email`,
    {
      method: "POST",
      body: JSON.stringify({ new_email: newEmail }),
    },
  );
}

export function disableUserTotp(userId: string) {
  return apiFetch<{ disabled: boolean }>(
    `/v1/admin/users/${userId}/disable-totp`,
    { method: "POST" },
  );
}

export function verifyUserEmail(userId: string) {
  return apiFetch<{ verified: boolean }>(
    `/v1/admin/users/${userId}/verify-email`,
    { method: "POST" },
  );
}

// --- Small actions (PR 3) ---

export interface ExplainAccountSystem {
  id: string;
  name: string;
  member_count: number;
  delete_confirmation: string;
  grace_period_days: number;
}

export interface ExplainAccountAuditRow {
  id: string;
  action: string;
  target_type: string;
  reason: string | null;
  created_at: string;
}

export interface ExplainAccountResponse {
  user_id: string;
  email: string;
  tier: string;
  is_admin: boolean;
  account_status: string;
  email_verified: boolean;
  totp_enabled: boolean;
  signup_ip: string | null;
  created_at: string;
  last_login_at: string | null;
  active_session_count: number;
  api_key_count: number;
  system: ExplainAccountSystem | null;
  recent_admin_audit: ExplainAccountAuditRow[];
}

export interface AdminUserSession {
  id: string;
  user_agent: string | null;
  ip: string | null;
  created_at: string | null;
  last_seen_at: string | null;
  nickname: string | null;
}

export function listUserSessionsAdmin(userId: string) {
  return apiFetch<AdminUserSession[]>(
    `/v1/admin/users/${userId}/sessions`,
  );
}

export function explainAccount(userId: string) {
  return apiFetch<ExplainAccountResponse>(
    `/v1/admin/users/${userId}/explain`,
  );
}

export function terminateUserSession(
  userId: string,
  sessionId: string,
  reason: string,
) {
  return apiFetch<{ revoked: boolean }>(
    `/v1/admin/users/${userId}/sessions/${sessionId}/terminate`,
    { method: "POST", body: JSON.stringify({ reason }) },
  );
}

export function forceRotateApiKeys(userId: string, reason: string) {
  return apiFetch<{ revoked_count: number }>(
    `/v1/admin/users/${userId}/api-keys/rotate-all`,
    { method: "POST", body: JSON.stringify({ reason }) },
  );
}

export interface BulkApproveResult {
  user_id: string;
  approved: boolean;
  reason: string | null;
}

export interface BulkApproveResponse {
  approved_count: number;
  results: BulkApproveResult[];
}

export function bulkApprove(userIds: string[]) {
  return apiFetch<BulkApproveResponse>("/v1/admin/approvals/bulk-approve", {
    method: "POST",
    body: JSON.stringify({ user_ids: userIds }),
  });
}

// --- Suspend / unsuspend (PR 4) ---

export interface SuspendResult {
  suspended: boolean;
  suspended_until: string | null;
  sessions_revoked: number | null;
}

export function suspendUser(
  userId: string,
  reason: string,
  durationDays: number | null,
) {
  return apiFetch<SuspendResult>(`/v1/admin/users/${userId}/suspend`, {
    method: "POST",
    body: JSON.stringify({
      reason,
      duration_days: durationDays,
    }),
  });
}

export interface UnsuspendResult {
  unsuspended: boolean;
  reason?: string;
}

export function unsuspendUser(userId: string, reason: string) {
  return apiFetch<UnsuspendResult>(`/v1/admin/users/${userId}/unsuspend`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

// --- Ban / unban (PR 5; permanent companion to suspend) ---

export interface BanResult {
  banned: boolean;
  sessions_revoked: number;
}

export function banUser(userId: string, reason: string) {
  return apiFetch<BanResult>(`/v1/admin/users/${userId}/ban`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export interface UnbanResult {
  unbanned: boolean;
  reason?: string;
}

export function unbanUser(userId: string, reason: string) {
  return apiFetch<UnbanResult>(`/v1/admin/users/${userId}/unban`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

// --- Dossier export (PR 4) ---

export async function downloadDossier(
  userId: string,
  reason: string,
): Promise<void> {
  // apiFetch coerces JSON; for a file download we want the raw blob,
  // so call fetch directly with the same credentials behaviour as the
  // shared client.
  const resp = await fetch(`/v1/admin/users/${userId}/dossier`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const j = (await resp.json()) as { detail?: string };
      if (j.detail) detail = j.detail;
    } catch {
      // body wasn't JSON
    }
    throw new Error(detail);
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  // The server sets Content-Disposition; pull the filename out so the
  // browser doesn't fall back to the URL path as the suggested name.
  const cd = resp.headers.get("content-disposition") ?? "";
  const match = cd.match(/filename="([^"]+)"/);
  a.download = match ? match[1] : `sheaf-dossier-${userId}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
