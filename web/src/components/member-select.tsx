import { useState } from "react";
import { useMembers } from "@/hooks/use-members";
import { useAllGroupMembers, useGroups } from "@/hooks/use-groups";
import { ColorDot } from "./color-dot";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

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
  const groupMemberMap = useAllGroupMembers();
  const [search, setSearch] = useState("");
  const [activeGroupId, setActiveGroupId] = useState<string | null>(null);

  if (!members) return null;

  function toggle(id: string) {
    if (selected.includes(id)) {
      onChange(selected.filter((s) => s !== id));
    } else {
      onChange([...selected, id]);
    }
  }

  const activeGroupMembers =
    activeGroupId !== null ? groupMemberMap.get(activeGroupId) : null;
  const searchLower = search.trim().toLowerCase();

  const filtered = members.filter((m) => {
    if (activeGroupMembers && !activeGroupMembers.has(m.id)) return false;
    if (searchLower) {
      return (
        m.name.toLowerCase().includes(searchLower) ||
        (m.display_name?.toLowerCase().includes(searchLower) ?? false)
      );
    }
    return true;
  });

  const hasGroups = showGroupFilter && (groups?.length ?? 0) > 0;

  return (
    <div className={cn("space-y-2", className)}>
      {hasGroups && (
        <div className="flex flex-wrap gap-1.5">
          <Badge
            variant={activeGroupId === null ? "default" : "outline"}
            className="cursor-pointer select-none"
            onClick={() => setActiveGroupId(null)}
          >
            All
          </Badge>
          {groups!.map((g) => (
            <Badge
              key={g.id}
              variant={activeGroupId === g.id ? "default" : "outline"}
              className="cursor-pointer select-none gap-1.5"
              onClick={() =>
                setActiveGroupId(activeGroupId === g.id ? null : g.id)
              }
            >
              {g.color && <ColorDot color={g.color} />}
              {g.name}
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
      <div className="flex flex-wrap gap-2">
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
