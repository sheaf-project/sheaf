import { toast } from "sonner";

import { getShowTechnicalErrors } from "./developer-prefs-snapshot";

let accessToken: string | null = null;
let refreshPromise: Promise<string | null> | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/**
 * Run the silent-refresh on app boot through the same single-flight that
 * apiFetch uses for 401-retry. Without this, StrictMode double-firing the
 * mount effect (or any other parallel-on-mount path) would send two
 * /v1/auth/refresh requests with the same cookie — the loser of the
 * server-side GETDEL would historically be treated as reuse and kill the
 * session. The backend now has a grace window for that, but deduping on
 * the client too keeps the cookie rotation tidy.
 */
export async function bootstrapAuth(): Promise<string | null> {
  if (!refreshPromise) {
    refreshPromise = refreshAccessToken();
  }
  const token = await refreshPromise;
  refreshPromise = null;
  return token;
}

/**
 * Refresh the access token using the HttpOnly refresh cookie.
 * The cookie is sent automatically by the browser — no localStorage involved.
 */
async function refreshAccessToken(): Promise<string | null> {
  try {
    const resp = await fetch("/v1/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
      credentials: "same-origin",
    });

    if (!resp.ok) {
      // Check if session was revoked
      if (resp.status === 401) {
        const body = await resp.json().catch(() => ({}));
        if (body.detail === "Session revoked") {
          accessToken = null;
          toast.error("Your session has been revoked. Redirecting to login...");
          setTimeout(() => {
            window.location.href = "/login";
          }, 1500);
          return null;
        }
      }
      accessToken = null;
      return null;
    }

    const data = await resp.json();
    accessToken = data.access_token;
    return accessToken;
  } catch {
    accessToken = null;
    return null;
  }
}

// ApiError lives in `./api-error` so non-api-client modules (e.g. the
// shared `showApiErrorToast` helper) can import the class without
// pulling in this file's fetch implementation. Re-exported here so
// existing imports of `ApiError` from `@/lib/api-client` keep working.
export { ApiError } from "./api-error";
import { ApiError } from "./api-error";

interface ApiFetchOptions extends RequestInit {
  /** Skip automatic token refresh on 401. Use for login/register endpoints. */
  skipRefresh?: boolean;
}

export async function apiFetch<T>(
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const { skipRefresh, ...fetchOptions } = options;
  const isFormData = fetchOptions.body instanceof FormData;
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { "Content-Type": "application/json" }),
    ...(fetchOptions.headers as Record<string, string>),
  };

  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  let resp = await fetch(path, { ...fetchOptions, headers, credentials: "same-origin" });

  // Auto-refresh on 401 using HttpOnly cookie (skip for login/register)
  let attemptedRefresh = false;
  if (resp.status === 401 && !skipRefresh) {
    attemptedRefresh = true;
    if (!refreshPromise) {
      refreshPromise = refreshAccessToken();
    }
    const newToken = await refreshPromise;
    refreshPromise = null;

    if (newToken) {
      headers["Authorization"] = `Bearer ${newToken}`;
      resp = await fetch(path, { ...fetchOptions, headers, credentials: "same-origin" });
    }
  }

  if (resp.status === 204) {
    return undefined as T;
  }

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: resp.statusText }));
    const detail = body.detail || "Request failed";

    // Session revoked — redirect to login
    if (resp.status === 401 && detail === "Session revoked") {
      accessToken = null;
      toast.error("Your session has been revoked. Redirecting to login...");
      setTimeout(() => {
        window.location.href = "/login";
      }, 1500);
      throw new ApiError(resp.status, detail);
    }

    // Show toast for non-auth errors (auth errors during login/register are
    // handled inline by the form, not via toast). Pre-retry 401 is handled
    // by the silent-refresh dance, and 409 is shown inline by the caller.
    if (resp.status === 401 && !attemptedRefresh) {
      // pre-retry 401: silent refresh handles this; no toast.
    } else if (resp.status === 409) {
      // 409: caller shows it inline.
    } else {
      autoToastError(resp.status, detail);
    }

    throw new ApiError(resp.status, detail);
  }

  return resp.json();
}

/**
 * Same auth + error handling as `apiFetch`, but returns both the parsed
 * body and the response headers. Use when the endpoint signals pagination
 * (or any other metadata) via headers — e.g. `GET /v1/fronts` returns
 * `X-Sheaf-Has-More` / `X-Sheaf-Next-Cursor` alongside the bare array body.
 */
export async function apiFetchWithHeaders<T>(
  path: string,
  options: ApiFetchOptions = {},
): Promise<{ body: T; headers: Headers }> {
  const { skipRefresh, ...fetchOptions } = options;
  const isFormData = fetchOptions.body instanceof FormData;
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { "Content-Type": "application/json" }),
    ...(fetchOptions.headers as Record<string, string>),
  };

  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  let resp = await fetch(path, {
    ...fetchOptions,
    headers,
    credentials: "same-origin",
  });

  let attemptedRefresh = false;
  if (resp.status === 401 && !skipRefresh) {
    attemptedRefresh = true;
    if (!refreshPromise) {
      refreshPromise = refreshAccessToken();
    }
    const newToken = await refreshPromise;
    refreshPromise = null;

    if (newToken) {
      headers["Authorization"] = `Bearer ${newToken}`;
      resp = await fetch(path, {
        ...fetchOptions,
        headers,
        credentials: "same-origin",
      });
    }
  }

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: resp.statusText }));
    const detail = body.detail || "Request failed";
    if (resp.status === 401 && detail === "Session revoked") {
      accessToken = null;
      toast.error("Your session has been revoked. Redirecting to login...");
      setTimeout(() => {
        window.location.href = "/login";
      }, 1500);
      throw new ApiError(resp.status, detail);
    }
    if (resp.status === 401 && !attemptedRefresh) {
      // pre-retry 401: silent refresh handles this; no toast.
    } else if (resp.status === 409) {
      // 409: caller shows it inline.
    } else {
      autoToastError(resp.status, detail);
    }
    throw new ApiError(resp.status, detail);
  }

  return { body: await resp.json(), headers: resp.headers };
}

// Inline mirror of the friendly-summary mapping used by the shared
// `showApiErrorToast` helper. Duplicated here (rather than imported)
// to avoid a cycle: api-errors.ts already imports ApiError, and
// importing showApiErrorToast back into api-client would close the
// loop. Both paths consult the same module-level snapshot so the
// behaviour stays consistent.
function autoToastError(httpStatus: number, detail: string): void {
  if (getShowTechnicalErrors()) {
    toast.error(`[${httpStatus}] ${detail}`);
    return;
  }
  if (httpStatus === 400) toast.error("Invalid request.");
  else if (httpStatus === 401) toast.error("You need to sign in to do that.");
  else if (httpStatus === 403) toast.error("You don't have permission to do that.");
  else if (httpStatus === 404) toast.error("Not found.");
  else if (httpStatus === 413) toast.error("That's too large.");
  else if (httpStatus === 422) toast.error("We couldn't understand that request.");
  else if (httpStatus === 423) toast.error("Account temporarily locked.");
  else if (httpStatus === 429) toast.error("Slow down — too many requests.");
  else if (httpStatus >= 500) toast.error("Server error — please try again.");
  else toast.error(detail);
}
