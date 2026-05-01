import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { useCreateFront } from "@/hooks/use-fronts";
import { ApiError } from "@/lib/api-client";
import { getMySystem } from "@/lib/systems";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { MemberSelect } from "@/components/member-select";

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
    error: string | null;
  }>({ wasOpen: false, members: [], replaceFronts: null, error: null });

  let { members: selectedMembers, replaceFronts, error } = draft;
  if (open && !draft.wasOpen) {
    selectedMembers = [];
    replaceFronts = null;
    error = null;
    setDraft({
      wasOpen: true,
      members: [],
      replaceFronts: null,
      error: null,
    });
  } else if (!open && draft.wasOpen) {
    setDraft({ ...draft, wasOpen: false });
  }

  const setSelectedMembers = (m: string[]) =>
    setDraft((d) => ({ ...d, members: m, error: null }));
  const setReplaceFronts = (r: boolean | null) =>
    setDraft((d) => ({ ...d, replaceFronts: r, error: null }));

  const effectiveReplace =
    replaceFronts ?? (system?.replace_fronts_default ?? true);

  function handleStart() {
    if (selectedMembers.length === 0) return;
    setDraft((d) => ({ ...d, error: null }));
    createFront.mutate(
      { member_ids: selectedMembers, replace_fronts: effectiveReplace },
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
        <MemberSelect
          selected={selectedMembers}
          onChange={setSelectedMembers}
          className="py-2"
          showGroupFilter
        />
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
