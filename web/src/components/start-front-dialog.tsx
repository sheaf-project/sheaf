import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { useCreateFront } from "@/hooks/use-fronts";
import { ApiError } from "@/lib/api-client";
import { getTopFronters } from "@/lib/members";
import { getMySystem } from "@/lib/systems";
import { cn } from "@/lib/utils";
import { ColorDot } from "@/components/color-dot";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MemberSelect } from "@/components/member-select";

/** One-tap chips for the members most likely to be picked — pinned
 *  members and recent fronters, from /v1/members/top-fronters. */
function QuickPick({
  open,
  selected,
  onToggle,
}: {
  open: boolean;
  selected: string[];
  onToggle: (id: string) => void;
}) {
  const { data: top } = useQuery({
    queryKey: ["members", "top-fronters"],
    queryFn: () => getTopFronters(8),
    enabled: open,
    staleTime: 60_000,
  });
  if (!top || top.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {top.map((m) => {
        const on = selected.includes(m.id);
        return (
          <button
            key={m.id}
            type="button"
            aria-pressed={on}
            onClick={() => onToggle(m.id)}
            className={cn(
              "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
              on
                ? "border-primary bg-primary/10"
                : "bg-background hover:bg-muted",
            )}
          >
            {m.emoji ? (
              <span aria-hidden>{m.emoji}</span>
            ) : (
              <ColorDot color={m.color} />
            )}
            <span>{m.display_name || m.name}</span>
          </button>
        );
      })}
    </div>
  );
}

export function StartFrontDialog({
  open,
  onOpenChange,
  onStarted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onStarted?: () => void;
}) {
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const createFront = useCreateFront();

  // Reset state on the open transition by tracking the previous value inline.
  // This is the recommended pattern when state needs to reinitialise from a
  // prop change without a useEffect+setState (which the lint blocks because
  // it cascades renders).
  const [draft, setDraft] = useState<{
    wasOpen: boolean;
    members: string[];
    replaceFronts: boolean | null;
    customStatus: string;
    error: string | null;
  }>({
    wasOpen: false,
    members: [],
    replaceFronts: null,
    customStatus: "",
    error: null,
  });

  let { members: selectedMembers, replaceFronts, customStatus, error } = draft;
  if (open && !draft.wasOpen) {
    selectedMembers = [];
    replaceFronts = null;
    customStatus = "";
    error = null;
    setDraft({
      wasOpen: true,
      members: [],
      replaceFronts: null,
      customStatus: "",
      error: null,
    });
  } else if (!open && draft.wasOpen) {
    setDraft({ ...draft, wasOpen: false });
  }

  const setSelectedMembers = (m: string[]) =>
    setDraft((d) => ({ ...d, members: m, error: null }));
  const setReplaceFronts = (r: boolean | null) =>
    setDraft((d) => ({ ...d, replaceFronts: r, error: null }));
  const setCustomStatus = (s: string) =>
    setDraft((d) => ({ ...d, customStatus: s, error: null }));

  const effectiveReplace =
    replaceFronts ?? (system?.replace_fronts_default ?? true);

  function handleStart() {
    if (selectedMembers.length === 0) return;
    setDraft((d) => ({ ...d, error: null }));
    createFront.mutate(
      {
        member_ids: selectedMembers,
        replace_fronts: effectiveReplace,
        custom_status: customStatus.trim() || null,
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          onStarted?.();
        },
        onError: (err) => {
          // 409 = duplicate front. Show inline so the user can fix it
          // without a toast that disappears. Other errors fall through to
          // the global toast handler in apiFetch.
          if (err instanceof ApiError && err.status === 409) {
            setDraft((d) => ({ ...d, error: err.detail }));
          }
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Start front</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          Select who is fronting. Pick multiple for co-fronting.
        </p>
        <QuickPick
          open={open}
          selected={selectedMembers}
          onToggle={(id) =>
            setSelectedMembers(
              selectedMembers.includes(id)
                ? selectedMembers.filter((x) => x !== id)
                : [...selectedMembers, id],
            )
          }
        />
        <MemberSelect
          selected={selectedMembers}
          onChange={setSelectedMembers}
          className="py-2"
          showGroupFilter
        />
        <div className="space-y-2 pt-1">
          <Label htmlFor="custom-status" className="text-sm font-normal">
            Custom status (optional)
          </Label>
          <Input
            id="custom-status"
            value={customStatus}
            onChange={(e) => setCustomStatus(e.target.value)}
            placeholder="e.g. during a job interview"
            maxLength={500}
          />
        </div>
        <div className="flex items-center gap-2 pt-1">
          <Checkbox
            id="replace-fronts"
            checked={effectiveReplace}
            onCheckedChange={(v) => setReplaceFronts(v === true)}
          />
          <Label
            htmlFor="replace-fronts"
            className="text-sm font-normal cursor-pointer"
          >
            End all current fronts
          </Label>
        </div>
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button
            onClick={handleStart}
            disabled={selectedMembers.length === 0 || createFront.isPending}
          >
            {createFront.isPending ? "Starting..." : "Start"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
