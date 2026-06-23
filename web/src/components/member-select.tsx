import { useState } from "react";
import { useMembers } from "@/hooks/use-members";
import { useAllGroupMembers, useGroups } from "@/hooks/use-groups";
import { useAllTagMembers, useTags } from "@/hooks/use-tags";
import { ColorDot } from "./color-dot";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  buildGroupTree,
  flattenGroupTree,
  getDescendantIds,
} from "@/lib/group-tree";

interface Props {
  selected: string[];
  onChange: (ids: string[]) => void;
  className?: string;
  showGroupFilter?: boolean;
}

export function MemberSelect({
  selected,
  onChange,
  className,
  showGroupFilter = false,
}: Props) {
  const { data: members } = useMembers();
  const { data: groups } = useGroups();
  const { data: tags } = useTags();
  const groupMemberMap = useAllGroupMembers();
  const tagMemberMap = useAllTagMembers();
  const [search, setSearch] = useState("");
  const [activeGroupId, setActiveGroupId] = useState<string | null>(null);
  const [activeTagId, setActiveTagId] = useState<string | null>(null);

  if (!members) return null;

  function toggle(id: string) {
    if (selected.includes(id)) {
      onChange(selected.filter((s) => s !== id));
    } else {
      onChange([...selected, id]);
    }
  }

  // Subtree-inclusive: selecting a group filters to members of it AND all
  // its descendant subgroups.
  const activeSubtreeIds =
    activeGroupId !== null
      ? getDescendantIds(activeGroupId, groups ?? [])
      : null;
  const activeGroupMembers =
    activeGroupId !== null
      ? (() => {
          const union = new Set<string>();
          for (const gid of [activeGroupId, ...(activeSubtreeIds ?? [])]) {
            const set = groupMemberMap.get(gid);
            if (set) for (const id of set) union.add(id);
          }
          return union;
        })()
      : null;
  const activeTagMembers =
    activeTagId !== null ? tagMemberMap.get(activeTagId) : null;
  const searchLower = search.trim().toLowerCase();

  const filtered = members.filter((m) => {
    // Archived members can't be newly assigned to a front.
    if (m.archived_at != null) return false;
    // Group + tag filters AND together — "in this group AND tagged this".
    if (activeGroupMembers && !activeGroupMembers.has(m.id)) return false;
    if (activeTagMembers && !activeTagMembers.has(m.id)) return false;
    if (searchLower) {
      return (
        m.name.toLowerCase().includes(searchLower) ||
        (m.display_name?.toLowerCase().includes(searchLower) ?? false)
      );
    }
    return true;
  });

  const hasGroups = showGroupFilter && (groups?.length ?? 0) > 0;
  const hasTags = showGroupFilter && (tags?.length ?? 0) > 0;

  return (
    <div className={cn("space-y-2", className)}>
      {hasGroups && (
        <div className="flex flex-wrap gap-1.5">
          <Badge
            variant={activeGroupId === null ? "default" : "outline"}
            className="cursor-pointer select-none"
            onClick={() => setActiveGroupId(null)}
          >
            All groups
          </Badge>
          {flattenGroupTree(buildGroupTree(groups!), new Set()).map(
            ({ group: g, depth }) => {
              const inActiveSubtree = activeSubtreeIds?.has(g.id) ?? false;
              return (
                <Badge
                  key={g.id}
                  variant={activeGroupId === g.id ? "default" : "outline"}
                  style={
                    depth > 0
                      ? { marginLeft: Math.min(depth, 4) * 10 }
                      : undefined
                  }
                  className={cn(
                    "cursor-pointer select-none gap-1.5",
                    inActiveSubtree && "ring-1 ring-primary/50",
                  )}
                  onClick={() =>
                    setActiveGroupId(activeGroupId === g.id ? null : g.id)
                  }
                >
                  {g.color && <ColorDot color={g.color} />}
                  {g.name}
                </Badge>
              );
            },
          )}
        </div>
      )}
      {hasTags && (
        <div className="flex flex-wrap gap-1.5">
          <Badge
            variant={activeTagId === null ? "default" : "outline"}
            className="cursor-pointer select-none"
            onClick={() => setActiveTagId(null)}
          >
            All tags
          </Badge>
          {tags!.map((t) => (
            <Badge
              key={t.id}
              variant={activeTagId === t.id ? "default" : "outline"}
              className="cursor-pointer select-none gap-1.5"
              onClick={() =>
                setActiveTagId(activeTagId === t.id ? null : t.id)
              }
            >
              {t.color && <ColorDot color={t.color} />}
              {t.name}
            </Badge>
          ))}
        </div>
      )}
      {members.length > 8 && (
        <Input
          placeholder="Search members…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-8"
          autoFocus
        />
      )}
      {/* Cap the picker height with its own scroll so a large roster doesn't
          push the surrounding controls (e.g. the Start button in the switch
          modal) below the fold. */}
      <div className="flex flex-wrap gap-2 max-h-[40vh] overflow-y-auto pr-1">
        {filtered.map((m) => (
          <Badge
            key={m.id}
            variant={selected.includes(m.id) ? "default" : "outline"}
            className="cursor-pointer select-none gap-1.5"
            onClick={() => toggle(m.id)}
          >
            <ColorDot color={m.color} />
            {m.display_name || m.name}
          </Badge>
        ))}
        {filtered.length === 0 && (
          <p className="text-sm text-muted-foreground">No members match.</p>
        )}
      </div>
    </div>
  );
}
