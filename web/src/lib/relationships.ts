import type {
  DeleteResult,
  DestructiveConfirm,
  RelationshipEdge,
  RelationshipEdgeCreate,
  RelationshipEdgeUpdate,
  RelationshipFromViewpoint,
  RelationshipGraph,
  RelationshipType,
  RelationshipTypeCreate,
  RelationshipTypeUpdate,
} from "@/types/api";

import { apiFetch } from "./api-client";

// --- Types ---

export function listRelationshipTypes() {
  return apiFetch<RelationshipType[]>("/v1/relationship-types");
}

export function createRelationshipType(data: RelationshipTypeCreate) {
  return apiFetch<RelationshipType>("/v1/relationship-types", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateRelationshipType(id: string, data: RelationshipTypeUpdate) {
  return apiFetch<RelationshipType>(`/v1/relationship-types/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

/** Delete a relationship type and every edge drawn with it.
 *
 *  Safeguarded: with the System Safety "relationships" category armed and a
 *  grace window set the server answers 202 with a pending action instead of
 *  deleting, so the result is `DeleteResult` (void or queued), not void. The
 *  optional confirm body carries the re-auth the current tier asks for. */
export function deleteRelationshipType(id: string, confirm?: DestructiveConfirm) {
  return apiFetch<DeleteResult>(`/v1/relationship-types/${id}`, {
    method: "DELETE",
    ...(confirm ? { body: JSON.stringify(confirm) } : {}),
  });
}

// --- Member edges ---

export function listMemberRelationships(memberId: string) {
  return apiFetch<RelationshipFromViewpoint[]>(
    `/v1/members/${memberId}/relationships`,
  );
}

/** Creating an edge straight to `public` runs the same step-up gate raising an
 *  existing one does, so it can come back with the same 400 asking for
 *  credentials - hence the same `skipErrorToast` escape the PATCH takes. */
export function createMemberRelationship(
  data: RelationshipEdgeCreate,
  skipErrorToast = false,
) {
  return apiFetch<RelationshipEdge>("/v1/member-relationships", {
    method: "POST",
    body: JSON.stringify(data),
    skipErrorToast,
  });
}

/** Move one member edge up or down the privacy ladder. A raise that would
 *  actually expose the edge is answered with a 400 asking for step-up
 *  credentials, so callers pass `skipErrorToast` and re-prompt instead of
 *  letting a toast fire for something the user can still complete. */
export function updateMemberRelationship(
  edgeId: string,
  data: RelationshipEdgeUpdate,
  skipErrorToast = false,
) {
  return apiFetch<RelationshipEdge>(`/v1/member-relationships/${edgeId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
    skipErrorToast,
  });
}

export function deleteMemberRelationship(edgeId: string) {
  return apiFetch<void>(`/v1/member-relationships/${edgeId}`, {
    method: "DELETE",
  });
}

// --- Group edges ---

export function listGroupRelationships(groupId: string) {
  return apiFetch<RelationshipFromViewpoint[]>(
    `/v1/groups/${groupId}/relationships`,
  );
}

/** The group twin. Never gated server-side (no share view reaches a group
 *  edge), but it takes the same option so both scopes call the same shape. */
export function createGroupRelationship(
  data: RelationshipEdgeCreate,
  skipErrorToast = false,
) {
  return apiFetch<RelationshipEdge>("/v1/group-relationships", {
    method: "POST",
    body: JSON.stringify(data),
    skipErrorToast,
  });
}

/** The group twin. Never deferred server-side (no share view reaches a group
 *  edge), but it takes the same option so both scopes call the same shape. */
export function updateGroupRelationship(
  edgeId: string,
  data: RelationshipEdgeUpdate,
  skipErrorToast = false,
) {
  return apiFetch<RelationshipEdge>(`/v1/group-relationships/${edgeId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
    skipErrorToast,
  });
}

export function deleteGroupRelationship(edgeId: string) {
  return apiFetch<void>(`/v1/group-relationships/${edgeId}`, {
    method: "DELETE",
  });
}

// --- Graph (for the viewer) ---

export function getRelationshipGraph(scope: "members" | "groups") {
  return apiFetch<RelationshipGraph>(`/v1/relationships/graph?scope=${scope}`);
}
