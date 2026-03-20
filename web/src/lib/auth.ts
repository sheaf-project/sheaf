import type { TokenResponse, User } from "@/types/api";
import { apiFetch } from "./api-client";

export function register(email: string, password: string) {
  return apiFetch<TokenResponse>("/v1/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
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
