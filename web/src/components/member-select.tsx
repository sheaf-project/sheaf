import { useState } from "react";
import { useMembers } from "@/hooks/use-members";
import { ColorDot } from "./color-dot";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

interface Props {
  selected: string[];
  onChange: (ids: string[]) => void;
  className?: string;
}

export function MemberSelect({ selected, onChange, className }: Props) {
  const { data: members } = useMembers();
  const [search, setSearch] = useState("");

  if (!members) return null;

  function toggle(id: string) {
    if (selected.includes(id)) {
      onChange(selected.filter((s) => s !== id));
    } else {
      onChange([...selected, id]);
    }
  }

  const filtered = search.trim()
    ? members.filter((m) =>
        m.name.toLowerCase().includes(search.toLowerCase()) ||
        (m.display_name?.toLowerCase().includes(search.toLowerCase()) ?? false)
      )
    : members;

  return (
    <div className={cn("space-y-2", className)}>
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
