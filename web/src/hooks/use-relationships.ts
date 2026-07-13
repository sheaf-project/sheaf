import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import * as api from "@/lib/relationships";
import type {
  RelationshipEdgeCreate,
  RelationshipTypeCreate,
  RelationshipTypeUpdate,
} from "@/types/api";

export const relationshipKeys = {
  types: ["relationship-types"] as const,
  memberEdges: (memberId: string) =>
    ["relationships", "member", memberId] as const,
  groupEdges: (groupId: string) => ["relationships", "group", groupId] as const,
  graph: (scope: string) => ["relationships", "graph", scope] as const,
};

// Broad invalidation on any edge change: the changed node's list, the graph,
// and (via the prefix) the other endpoint's list, which also holds this edge.
function invalidateEdges(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["relationships"] });
}

// --- Types ---

export function useRelationshipTypes() {
  return useQuery({
    queryKey: relationshipKeys.types,
    queryFn: api.listRelationshipTypes,
  });
}

export function useCreateRelationshipType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: RelationshipTypeCreate) =>
      api.createRelationshipType(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: relationshipKeys.types });
      toast.success("Relationship type created");
    },
  });
}

export function useUpdateRelationshipType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: RelationshipTypeUpdate }) =>
      api.updateRelationshipType(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: relationshipKeys.types });
      invalidateEdges(qc); // labels/type_name shown on edges may have changed
      toast.success("Relationship type updated");
    },
  });
}

export function useDeleteRelationshipType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteRelationshipType(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: relationshipKeys.types });
      invalidateEdges(qc); // deleting a type cascades its edges
      toast.success("Relationship type deleted");
    },
  });
}

// --- Member edges ---

export function useMemberRelationships(memberId: string | null) {
  return useQuery({
    queryKey: relationshipKeys.memberEdges(memberId ?? ""),
    queryFn: () => api.listMemberRelationships(memberId!),
    enabled: !!memberId,
  });
}

export function useCreateMemberRelationship() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: RelationshipEdgeCreate) =>
      api.createMemberRelationship(data),
    onSuccess: () => {
      invalidateEdges(qc);
      toast.success("Relationship added");
    },
  });
}

export function useDeleteMemberRelationship() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (edgeId: string) => api.deleteMemberRelationship(edgeId),
    onSuccess: () => {
      invalidateEdges(qc);
      toast.success("Relationship removed");
    },
  });
}

// --- Group edges ---

export function useGroupRelationships(groupId: string | null) {
  return useQuery({
    queryKey: relationshipKeys.groupEdges(groupId ?? ""),
    queryFn: () => api.listGroupRelationships(groupId!),
    enabled: !!groupId,
  });
}

export function useCreateGroupRelationship() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: RelationshipEdgeCreate) =>
      api.createGroupRelationship(data),
    onSuccess: () => {
      invalidateEdges(qc);
      toast.success("Relationship added");
    },
  });
}

export function useDeleteGroupRelationship() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (edgeId: string) => api.deleteGroupRelationship(edgeId),
    onSuccess: () => {
      invalidateEdges(qc);
      toast.success("Relationship removed");
    },
  });
}

// --- Graph ---

export function useRelationshipGraph(scope: "members" | "groups") {
  return useQuery({
    queryKey: relationshipKeys.graph(scope),
    queryFn: () => api.getRelationshipGraph(scope),
  });
}
