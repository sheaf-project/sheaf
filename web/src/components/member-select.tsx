import { useMembers } from "@/hooks/use-members";
import { ColorDot } from "./color-dot";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface Props {
  selected: string[];
  onChange: (ids: string[]) => void;
  className?: string;
}

export function MemberSelect({ selected, onChange, className }: Props) {
  const { data: members } = useMembers();

  if (!members) return null;

  function toggle(id: string) {
    if (selected.includes(id)) {
      onChange(selected.filter((s) => s !== id));
    } else {
      onChange([...selected, id]);
    }
  }

  return (
    <div className={cn("flex flex-wrap gap-2", className)}>
      {members.map((m) => (
        <Badge
          key={m.id}
          variant={selected.includes(m.id) ? "default" : "outline"}
          className="cursor-pointer select-none gap-1.5"
          onClick={() => toggle(m.id)}
        >
          <ColorDot color={m.color} />
          {m.name}
        </Badge>
      ))}
    </div>
  );
}
