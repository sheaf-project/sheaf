import type {
  ManageChannelView,
  RedeemPreview,
  RedeemRequest,
  RedeemResponse,
} from "@/types/api";

// These endpoints are unauthenticated, so don't go through apiFetch (which
// would attach the access token / hit the refresh path on 401).
async function publicFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers as Record<string, string> | undefined),
    },
    credentials: "same-origin",
  });
  if (resp.status === 204) {
    return undefined as T;
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(body.detail || "Request failed");
  }
  return resp.json();
}

export function redeemActivation(data: RedeemRequest) {
  return publicFetch<RedeemResponse>("/v1/notifications/redeem", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function previewActivation(code: string) {
  return publicFetch<RedeemPreview>(
    `/v1/notifications/redeem-preview?code=${encodeURIComponent(code)}`,
  );
}

export function viewManagedChannel(token: string) {
  return publicFetch<ManageChannelView>(
    `/v1/notifications/manage/${encodeURIComponent(token)}`,
  );
}

export function unsubscribeManagedChannel(token: string) {
  return publicFetch<void>(
    `/v1/notifications/manage/${encodeURIComponent(token)}/unsubscribe`,
    { method: "POST" },
  );
}
