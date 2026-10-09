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
  /** Operator-authored markdown, shown from the public profile footer. */
  abuse_contact: string | null;
  support_email: string | null;
  support_url: string | null;
  support_note: string | null;
  support_custom_text: string | null;
  status_url: string | null;
  captcha_provider: string | null;
  captcha_on_login: boolean;
  /** Whether this instance offers passkey sign-in and enrolment. False hides
   *  every passkey control; the reason is for operators, not the UI. */
  passkeys_available: boolean;
  passkeys_unavailable_reason: string | null;
}

export function getAuthConfig() {
  return apiFetch<AuthConfig>("/v1/auth/config", { skipRefresh: true });
}

export function register(
  email: string,
  password: string,
  invite_code?: string,
  newsletter_opt_in: boolean = false,
  captcha?: string,
) {
  return apiFetch<TokenResponse>("/v1/auth/register", {
    method: "POST",
    skipRefresh: true,
    body: JSON.stringify({
      email,
      password,
      newsletter_opt_in,
      ...(invite_code ? { invite_code } : {}),
      ...(captcha ? { captcha } : {}),
    }),
  });
}

export function updateMe(update: {
  newsletter_opt_in?: boolean;
  disable_cdn_during_ddos?: boolean;
}) {
  return apiFetch<User>("/v1/auth/me", {
    method: "PATCH",
    body: JSON.stringify(update),
  });
}

export function login(
  email: string,
  password: string,
  totp_code?: string,
  captcha?: string,
  remember_device?: boolean,
  device_nickname?: string,
) {
  return apiFetch<TokenResponse>("/v1/auth/login", {
    method: "POST",
    skipRefresh: true,
    body: JSON.stringify({
      email,
      password,
      ...(totp_code ? { totp_code } : {}),
      ...(captcha ? { captcha } : {}),
      ...(remember_device ? { remember_device } : {}),
      ...(device_nickname ? { device_nickname } : {}),
    }),
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

export function totpSetup(password: string) {
  // Enabling 2FA is password-gated server-side so a stolen session can't
  // enrol an attacker-controlled factor.
  return apiFetch<TOTPSetupResponse>("/v1/auth/totp/setup", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
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

export function revalidateEmail() {
  return apiFetch<{ sent: boolean }>("/v1/auth/revalidate-email", { method: "POST" });
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

export function changeEmail(
  new_email: string,
  current_password: string,
  totp_code?: string,
) {
  return apiFetch<{
    email: string;
    verification_sent: boolean;
    revoked_other_sessions: number;
  }>("/v1/auth/change-email", {
    method: "POST",
    body: JSON.stringify({ new_email, current_password, totp_code }),
  });
}

export function changePassword(
  current_password: string,
  new_password: string,
  totp_code?: string,
) {
  return apiFetch<{ changed: boolean; revoked_other_sessions: number }>(
    "/v1/auth/change-password",
    {
      method: "POST",
      body: JSON.stringify({ current_password, new_password, totp_code }),
    },
  );
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

export interface TrustedDevice {
  id: string;
  nickname: string | null;
  /** Friendly client identifier, e.g. "Sheaf Android", "Firefox".
   *  Populated at mint time from X-Sheaf-Client when supplied; falls
   *  back to a User-Agent parse for legacy rows (and on the server
   *  side, for rows that pre-date the column). Prefer this in the UI
   *  over user_agent. */
  client_name: string;
  user_agent: string;
  created_at: string;
  created_ip: string | null;
  last_used_at: string | null;
  last_used_ip: string | null;
  expires_at: string;
  is_current: boolean;
}

export function getTrustedDevices() {
  return apiFetch<TrustedDevice[]>("/v1/auth/trusted-devices");
}

export function renameTrustedDevice(id: string, nickname: string) {
  return apiFetch<{ ok: boolean }>(`/v1/auth/trusted-devices/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ nickname }),
  });
}

export function revokeTrustedDevice(id: string) {
  return apiFetch<void>(`/v1/auth/trusted-devices/${id}`, { method: "DELETE" });
}

export function revokeAllTrustedDevices() {
  return apiFetch<{ revoked: number }>("/v1/auth/trusted-devices/revoke-all", {
    method: "POST",
  });
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
// Passkeys
// ---------------------------------------------------------------------------

export interface Passkey {
  id: string;
  nickname: string | null;
  /** The relying-party ID the credential was created under, and whether it
   *  matches this instance's current one. A domain move leaves old rows
   *  listed with `usable` false so the owner can see why they stopped
   *  working and re-enrol. */
  rp_id: string;
  usable: boolean;
  transports: string[];
  aaguid: string | null;
  /** Synced passkey (phone keychain, password manager) versus a single
   *  device or hardware key. */
  backup_eligible: boolean;
  backup_state: boolean;
  created_at: string;
  last_used_at: string | null;
}

export function listPasskeys() {
  return apiFetch<Passkey[]>("/v1/auth/passkeys");
}

/** Step up (password, plus a TOTP or recovery code when the account has
 *  TOTP) and receive the options for `navigator.credentials.create()`. */
export function passkeyRegisterBegin(password: string, totp_code?: string) {
  return apiFetch<{ options: Record<string, unknown> }>("/v1/auth/passkeys/register/begin", {
    method: "POST",
    skipErrorToast: true,
    body: JSON.stringify({ password, ...(totp_code ? { totp_code } : {}) }),
  });
}

export function passkeyRegisterComplete(
  credential: Record<string, unknown>,
  nickname?: string,
) {
  return apiFetch<Passkey>("/v1/auth/passkeys/register/complete", {
    method: "POST",
    skipErrorToast: true,
    body: JSON.stringify({ credential, ...(nickname ? { nickname } : {}) }),
  });
}

export function renamePasskey(id: string, nickname: string) {
  return apiFetch<Passkey>(`/v1/auth/passkeys/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ nickname }),
  });
}

/** Never password-gated, and the last one may go: the password remains. */
export function deletePasskey(id: string) {
  return apiFetch<void>(`/v1/auth/passkeys/${id}`, { method: "DELETE" });
}

/** No body and no account: the credential is discoverable. */
export function passkeySignInBegin() {
  return apiFetch<{ options: Record<string, unknown> }>("/v1/auth/passkeys/sign-in/begin", {
    method: "POST",
    skipRefresh: true,
    skipErrorToast: true,
  });
}

export function passkeySignInComplete(credential: Record<string, unknown>, captcha?: string) {
  return apiFetch<TokenResponse>("/v1/auth/passkeys/sign-in/complete", {
    method: "POST",
    skipRefresh: true,
    skipErrorToast: true,
    body: JSON.stringify({ credential, ...(captcha ? { captcha } : {}) }),
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
