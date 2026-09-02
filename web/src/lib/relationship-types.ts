import type { RelationshipSymmetry } from "@/types/api";

/** A one-line, plain summary of how a relationship type reads. Shared by
 *  every list of types (Settings and the graph page's Manage types dialog).
 *  Lives here rather than in the dialog component file so fast refresh keeps
 *  working there (component files must only export components). */
export function summariseType(t: {
  symmetry: RelationshipSymmetry;
  forward_label: string;
  reverse_label: string | null;
}): string {
  if (t.symmetry === "symmetric") return t.forward_label;
  return `${t.forward_label} -> ${t.reverse_label ?? "?"}`;
}
