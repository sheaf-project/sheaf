import { useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Member } from "@/types/api";

interface MemberPillsInputProps {
  members: Member[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  placeholder?: string;
  className?: string;
  id?: string;
}

function memberLabel(m: Member): string {
  return m.display_name || m.name;
}

export function MemberPillsInput({
  members,
  selectedIds,
  onChange,
  placeholder = "Add member…",
  className,
  id,
}: MemberPillsInputProps) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const byId = useMemo(() => {
    const m = new Map<string, Member>();
    for (const member of members) m.set(member.id, member);
    return m;
  }, [members]);

  const selected = useMemo(
    () => selectedIds.map((id) => byId.get(id)).filter((m): m is Member => !!m),
    [selectedIds, byId],
  );

  const candidates = useMemo(() => {
    const q = query.trim().toLowerCase();
    return members
      .filter((m) => !selectedIds.includes(m.id))
      .filter((m) => {
        if (!q) return true;
        const label = memberLabel(m).toLowerCase();
        return label.includes(q) || m.name.toLowerCase().includes(q);
      })
      .slice(0, 10);
  }, [members, selectedIds, query]);

  const activeIndex =
    candidates.length === 0 ? 0 : Math.min(highlighted, candidates.length - 1);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  function add(id: string) {
    if (selectedIds.includes(id)) return;
    onChange([...selectedIds, id]);
    setQuery("");
    setOpen(true);
    inputRef.current?.focus();
  }

  function remove(id: string) {
    onChange(selectedIds.filter((x) => x !== id));
    inputRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Backspace" && query === "" && selected.length > 0) {
      e.preventDefault();
      remove(selected[selected.length - 1].id);
      return;
    }
    if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      setOpen(true);
      return;
    }
    if (open && candidates.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setHighlighted((activeIndex + 1) % candidates.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setHighlighted(
          (activeIndex - 1 + candidates.length) % candidates.length,
        );
      } else if (e.key === "Enter") {
        e.preventDefault();
        const pick = candidates[activeIndex];
        if (pick) add(pick.id);
      } else if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
      }
    }
  }

  return (
    <div ref={containerRef} className={cn("relative", className)}>
      <div
        className="flex flex-wrap items-center gap-1 rounded-md border border-input bg-background px-2 py-1.5 text-sm focus-within:ring-2 focus-within:ring-ring/50"
        onClick={() => inputRef.current?.focus()}
      >
        {selected.map((m) => (
          <span
            key={m.id}
            className="inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-0.5 text-xs text-secondary-foreground"
          >
            {memberLabel(m)}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                remove(m.id);
              }}
              className="-mr-1 rounded-full opacity-60 hover:opacity-100"
              aria-label={`Remove ${memberLabel(m)}`}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          id={id}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setHighlighted(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder={selected.length === 0 ? placeholder : ""}
          className="flex-1 min-w-[8ch] bg-transparent text-sm outline-none"
        />
      </div>
      {open && candidates.length > 0 && (
        <ul
          className="absolute z-10 mt-1 max-h-56 w-full overflow-auto rounded-md border bg-popover py-1 text-popover-foreground shadow-md"
          role="listbox"
        >
          {candidates.map((m, i) => (
            <li
              key={m.id}
              role="option"
              aria-selected={i === activeIndex}
              onMouseDown={(e) => {
                e.preventDefault();
                add(m.id);
              }}
              onMouseEnter={() => setHighlighted(i)}
              className={cn(
                "cursor-pointer px-3 py-1.5 text-sm",
                i === activeIndex
                  ? "bg-accent text-accent-foreground"
                  : "hover:bg-accent hover:text-accent-foreground",
              )}
            >
              {memberLabel(m)}
              {m.display_name && m.display_name !== m.name && (
                <span className="ml-2 text-xs text-muted-foreground">
                  ({m.name})
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
