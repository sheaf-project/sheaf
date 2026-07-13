import type {
  RelationshipEdge,
  RelationshipEdgeCreate,
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

export function deleteRelationshipType(id: string) {
  return apiFetch<void>(`/v1/relationship-types/${id}`, { method: "DELETE" });
}

// --- Member edges ---

export function listMemberRelationships(memberId: string) {
  return apiFetch<RelationshipFromViewpoint[]>(
    `/v1/members/${memberId}/relationships`,
  );
}

export function createMemberRelationship(data: RelationshipEdgeCreate) {
  return apiFetch<RelationshipEdge>("/v1/member-relationships", {
    method: "POST",
    body: JSON.stringify(data),
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

export function createGroupRelationship(data: RelationshipEdgeCreate) {
  return apiFetch<RelationshipEdge>("/v1/group-relationships", {
    method: "POST",
    body: JSON.stringify(data),
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
