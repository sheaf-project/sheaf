import type { Group } from "@/types/api";

// Helpers for the group hierarchy (subgroups). Groups carry a self
// `parent_id`; these turn the flat list the API returns into a tree and
// answer the descendant questions the UI needs (subtree-inclusive member
// filtering, cycle-safe reparent targets). All defensive against a
// parent_id that points at a missing group: such a group is treated as a
// root so the tree never silently drops it.

export interface GroupTreeNode {
  group: Group;
  depth: number;
  children: GroupTreeNode[];
}

function byName(a: Group, b: Group): number {
  return a.name.localeCompare(b.name);
}

export function buildGroupTree(groups: Group[]): GroupTreeNode[] {
  const ids = new Set(groups.map((g) => g.id));
  const childrenOf = new Map<string, Group[]>();
  const roots: Group[] = [];
  for (const g of groups) {
    if (g.parent_id && ids.has(g.parent_id)) {
      const arr = childrenOf.get(g.parent_id) ?? [];
      arr.push(g);
      childrenOf.set(g.parent_id, arr);
    } else {
      roots.push(g);
    }
  }
  const build = (g: Group, depth: number): GroupTreeNode => ({
    group: g,
    depth,
    children: (childrenOf.get(g.id) ?? [])
      .slice()
      .sort(byName)
      .map((c) => build(c, depth + 1)),
  });
  return roots.sort(byName).map((g) => build(g, 0));
}

/** Every group nested under `groupId` (not including it). */
export function getDescendantIds(groupId: string, groups: Group[]): Set<string> {
  const childrenOf = new Map<string, string[]>();
  for (const g of groups) {
    if (g.parent_id) {
      const arr = childrenOf.get(g.parent_id) ?? [];
      arr.push(g.id);
      childrenOf.set(g.parent_id, arr);
    }
  }
  const out = new Set<string>();
  const stack = [...(childrenOf.get(groupId) ?? [])];
  while (stack.length) {
    const id = stack.pop()!;
    if (out.has(id)) continue;
    out.add(id);
    stack.push(...(childrenOf.get(id) ?? []));
  }
  return out;
}

export interface FlatGroupRow {
  group: Group;
  depth: number;
  hasChildren: boolean;
}

/** Depth-first rows for rendering, skipping the children of collapsed nodes. */
export function flattenGroupTree(
  nodes: GroupTreeNode[],
  collapsed: Set<string>,
): FlatGroupRow[] {
  const out: FlatGroupRow[] = [];
  const walk = (ns: GroupTreeNode[]) => {
    for (const n of ns) {
      out.push({
        group: n.group,
        depth: n.depth,
        hasChildren: n.children.length > 0,
      });
      if (n.children.length && !collapsed.has(n.group.id)) walk(n.children);
    }
  };
  walk(nodes);
  return out;
}
