import type { TokenResponse, User } from "@/types/api";
import { apiFetch } from "./api-client";

export function register(email: string, password: string) {
  return apiFetch<TokenResponse>("/v1/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function login(email: string, password: string) {
  return apiFetch<TokenResponse>("/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function getMe() {
  return apiFetch<User>("/v1/auth/me");
}

export function logout() {
  return apiFetch<void>("/v1/auth/logout", { method: "POST" });
}
