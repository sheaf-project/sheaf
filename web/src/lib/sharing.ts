import type {
  AdultAttestation,
  ShareAudit,
  ShareGrant,
  ShareGrantCreate,
  ShareGrantCreated,
  SharePreview,
  ShareView,
  ShareViewCreate,
  ShareViewGroupAddResult,
  ShareViewUpdate,
} from "@/types/api";

import { apiFetch } from "./api-client";

// --- Attestation ---

export function attestAdult() {
  return apiFetch<AdultAttestation>("/v1/auth/me/attest-adult", {
    method: "POST",
  });
}

// --- Views ---

export function listShareViews() {
  return apiFetch<ShareView[]>("/v1/share-views");
}

export function getShareView(id: string) {
  return apiFetch<ShareView>(`/v1/share-views/${id}`);
}

export function createShareView(data: ShareViewCreate) {
  return apiFetch<ShareView>("/v1/share-views", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateShareView(id: string, data: ShareViewUpdate) {
  return apiFetch<ShareView>(`/v1/share-views/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function deleteShareView(id: string) {
  return apiFetch<void>(`/v1/share-views/${id}`, { method: "DELETE" });
}

// --- View contents ---

export function addViewMember(viewId: string, memberId: string, reauth?: { password?: string; totp_code?: string }) {
  return apiFetch<ShareView>(`/v1/share-views/${viewId}/members`, {
    method: "POST",
    body: JSON.stringify({ member_id: memberId, ...reauth }),
  });
}

export function removeViewMember(viewId: string, memberId: string) {
  return apiFetch<void>(`/v1/share-views/${viewId}/members/${memberId}`, {
    method: "DELETE",
  });
}

export function addViewGroup(viewId: string, groupId: string, reauth?: { password?: string; totp_code?: string }) {
  return apiFetch<ShareViewGroupAddResult>(`/v1/share-views/${viewId}/groups`, {
    method: "POST",
    body: JSON.stringify({ group_id: groupId, ...reauth }),
  });
}

export function removeViewGroup(viewId: string, groupId: string, removeMembers = true) {
  return apiFetch<void>(
    `/v1/share-views/${viewId}/groups/${groupId}?remove_members=${removeMembers}`,
    { method: "DELETE" },
  );
}

export function addViewField(viewId: string, fieldId: string, reauth?: { password?: string; totp_code?: string }) {
  return apiFetch<ShareView>(`/v1/share-views/${viewId}/fields`, {
    method: "POST",
    body: JSON.stringify({ field_id: fieldId, ...reauth }),
  });
}

export function removeViewField(viewId: string, fieldId: string) {
  return apiFetch<void>(`/v1/share-views/${viewId}/fields/${fieldId}`, {
    method: "DELETE",
  });
}

// --- Preview ---

/**
 * This view exactly as a visitor would receive it, built by the server from the
 * same projection the anonymous surface uses.
 *
 * Note what it does NOT need: a grant, or the instance's public switch. Looking
 * at what you are about to publish before you publish it is the point of the
 * thing, so an unpublished view previews fine - and `suppressed` in the
 * response says when the page would not actually be reachable.
 */
export function getSharePreview(viewId: string) {
  return apiFetch<SharePreview>(`/v1/share-views/${viewId}/preview`);
}

// --- Grants ---

export function listShareGrants() {
  return apiFetch<ShareGrant[]>("/v1/share-grants");
}

export function createShareGrant(data: ShareGrantCreate) {
  return apiFetch<ShareGrantCreated>("/v1/share-grants", {
    method: "POST",
    body: JSON.stringify(data),
    // Every way this fails is something the owner has to read and act on -
    // system privacy still private, the 18+ confirmation, the grant cap, an
    // existing public profile, a wrong re-auth password. The publish dialog
    // stays open on failure and shows the reason in place, so a toast on top
    // of it would be a second copy of the same message, in the wrong spot.
    skipErrorToast: true,
  });
}

export function rotateShareGrant(id: string) {
  return apiFetch<ShareGrantCreated>(`/v1/share-grants/${id}/rotate`, {
    method: "POST",
  });
}

export function revokeShareGrant(id: string) {
  return apiFetch<void>(`/v1/share-grants/${id}`, { method: "DELETE" });
}

// --- Audit ---

export function getShareAudit() {
  return apiFetch<ShareAudit>("/v1/sharing/audit");
}
