import { toast } from "sonner";

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

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

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
    // handled inline by the form, not via toast).
    if (resp.status >= 500) {
      toast.error("Server error — please try again");
    } else if (resp.status === 401 && attemptedRefresh) {
      // Post-retry 401: refresh succeeded but the action itself still
      // came back unauthorized. Not an auth-flow concern (the silent
      // refresh already ran) — surface it like any other error so the
      // user sees what went wrong instead of clicking into silence.
      toast.error(detail);
    } else if (resp.status !== 401 && resp.status !== 409) {
      // Skip pre-retry 401 (the refresh dance handles it) and 409
      // (conflict, shown inline by the caller).
      toast.error(detail);
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
    if (resp.status >= 500) {
      toast.error("Server error — please try again");
    } else if (resp.status === 401 && attemptedRefresh) {
      // Post-retry 401: refresh succeeded but the action itself still
      // came back unauthorized. Surface it like any other error.
      toast.error(detail);
    } else if (resp.status !== 401 && resp.status !== 409) {
      toast.error(detail);
    }
    throw new ApiError(resp.status, detail);
  }

  return { body: await resp.json(), headers: resp.headers };
}
