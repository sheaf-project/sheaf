import type { TokenResponse, User } from "@/types/api";
import { apiFetch } from "./api-client";

export interface AuthConfig {
  registration_mode: string;
  invite_codes_enabled: boolean;
  email_verification: string;
  email_enabled: boolean;
  account_deletion_grace_days: number;
  file_cdn_base: string | null;
  terms_url: string | null;
  privacy_url: string | null;
}

export function getAuthConfig() {
  return apiFetch<AuthConfig>("/v1/auth/config", { skipRefresh: true });
}

export function register(
  email: string,
  password: string,
  invite_code?: string,
  newsletter_opt_in: boolean = false,
) {
  return apiFetch<TokenResponse>("/v1/auth/register", {
    method: "POST",
    skipRefresh: true,
    body: JSON.stringify({
      email,
      password,
      newsletter_opt_in,
      ...(invite_code ? { invite_code } : {}),
    }),
  });
}

export function updateMe(update: { newsletter_opt_in?: boolean }) {
  return apiFetch<User>("/v1/auth/me", {
    method: "PATCH",
    body: JSON.stringify(update),
  });
}

export function login(email: string, password: string, totp_code?: string) {
  return apiFetch<TokenResponse>("/v1/auth/login", {
    method: "POST",
    skipRefresh: true,
    body: JSON.stringify({ email, password, ...(totp_code ? { totp_code } : {}) }),
  });
}

export function getMe() {
  return apiFetch<User>("/v1/auth/me");
}

export function logout() {
  return apiFetch<void>("/v1/auth/logout", { method: "POST" });
}

export interface TOTPSetupResponse {
  secret: string;
  provisioning_uri: string;
  recovery_codes: string[];
}

export function totpSetup() {
  return apiFetch<TOTPSetupResponse>("/v1/auth/totp/setup", { method: "POST" });
}

export function totpVerify(code: string) {
  return apiFetch<void>("/v1/auth/totp/verify", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export function totpDisable(email: string, password: string, totp_code: string) {
  return apiFetch<void>("/v1/auth/totp/disable", {
    method: "POST",
    body: JSON.stringify({ email, password, totp_code }),
  });
}

export function resendVerification() {
  return apiFetch<{ sent: boolean }>("/v1/auth/resend-verification", { method: "POST" });
}

export function verifyEmail(token: string) {
  return apiFetch<{ verified: boolean }>(
    `/v1/auth/verify-email?token=${encodeURIComponent(token)}`,
    { skipRefresh: true },
  );
}

export function requestPasswordReset(email: string) {
  return apiFetch<{ requested: boolean }>("/v1/auth/request-password-reset", {
    method: "POST",
    skipRefresh: true,
    body: JSON.stringify({ email }),
  });
}

export function resetPassword(token: string, new_password: string) {
  return apiFetch<{ reset: boolean }>("/v1/auth/reset-password", {
    method: "POST",
    skipRefresh: true,
    body: JSON.stringify({ token, new_password }),
  });
}

export function regenerateRecoveryCodes(totp_code: string) {
  return apiFetch<{ recovery_codes: string[] }>(
    "/v1/auth/totp/regenerate-recovery-codes",
    {
      method: "POST",
      body: JSON.stringify({ code: totp_code }),
    },
  );
}

// ---------------------------------------------------------------------------
// Session management
// ---------------------------------------------------------------------------

export interface Session {
  id: string;
  nickname: string | null;
  client_name: string;
  created_at: string;
  created_ip: string | null;
  last_active_at: string;
  last_active_ip: string | null;
  is_current: boolean;
}

export function getSessions() {
  return apiFetch<Session[]>("/v1/auth/sessions");
}

export function renameSession(id: string, nickname: string) {
  return apiFetch<{ ok: boolean }>(`/v1/auth/sessions/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ nickname }),
  });
}

export function revokeSession(id: string) {
  return apiFetch<void>(`/v1/auth/sessions/${id}`, { method: "DELETE" });
}

export function revokeOtherSessions() {
  return apiFetch<{ revoked: number }>("/v1/auth/sessions/revoke-others", {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Account deletion
// ---------------------------------------------------------------------------

export function requestAccountDeletion(
  password: string,
  totp_code?: string,
) {
  return apiFetch<{ deletion_scheduled_for: string; grace_days: number }>(
    "/v1/auth/delete-account",
    {
      method: "POST",
      body: JSON.stringify({
        password,
        ...(totp_code ? { totp_code } : {}),
      }),
    },
  );
}

export function cancelDeletion() {
  return apiFetch<{ cancelled: boolean }>("/v1/auth/cancel-deletion", {
    method: "POST",
  });
}
