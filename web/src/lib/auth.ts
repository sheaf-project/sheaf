import type { TokenResponse, User } from "@/types/api";
import { apiFetch } from "./api-client";

export interface AuthConfig {
  registration_mode: string;
  invite_codes_enabled: boolean;
  email_verification: string;
  email_enabled: boolean;
}

export function getAuthConfig() {
  return apiFetch<AuthConfig>("/v1/auth/config");
}

export function register(
  email: string,
  password: string,
  invite_code?: string,
) {
  return apiFetch<TokenResponse>("/v1/auth/register", {
    method: "POST",
    body: JSON.stringify({
      email,
      password,
      ...(invite_code ? { invite_code } : {}),
    }),
  });
}

export function login(email: string, password: string, totp_code?: string) {
  return apiFetch<TokenResponse>("/v1/auth/login", {
    method: "POST",
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
  return apiFetch<{ verified: boolean }>(`/v1/auth/verify-email?token=${encodeURIComponent(token)}`);
}

export function requestPasswordReset(email: string) {
  return apiFetch<{ requested: boolean }>("/v1/auth/request-password-reset", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function resetPassword(token: string, new_password: string) {
  return apiFetch<{ reset: boolean }>("/v1/auth/reset-password", {
    method: "POST",
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
