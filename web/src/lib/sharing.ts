import type {
  AdultAttestation,
  ShareAudit,
  ShareGrant,
  ShareGrantCreate,
  ShareGrantCreated,
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

// --- Grants ---

export function listShareGrants() {
  return apiFetch<ShareGrant[]>("/v1/share-grants");
}

export function createShareGrant(data: ShareGrantCreate) {
  return apiFetch<ShareGrantCreated>("/v1/share-grants", {
    method: "POST",
    body: JSON.stringify(data),
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
