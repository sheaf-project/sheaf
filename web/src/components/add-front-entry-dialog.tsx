import { useState } from "react";

import { useDateFormatters } from "@/hooks/use-date-formatters";
import { useCreateFront } from "@/hooks/use-fronts";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MemberSelect } from "@/components/member-select";

/**
 * Record a front that already happened.
 *
 * Deliberately separate from starting a front: this always writes a CLOSED
 * entry, so it cannot end whoever is fronting right now and cannot become the
 * current front. Both times are required for that reason - an entry with no
 * end would be an open front, which is a switch, not a piece of history.
 */
export function AddFrontEntryDialog({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved?: () => void;
}) {
  const createFront = useCreateFront();
  // datetime-local values are rendered/parsed in the display timezone, so the
  // times a user reads elsewhere match the times they enter here.
  const { fromDateTimeLocal, timeZone } = useDateFormatters();
  const [error, setError] = useState("");

  // Reinit on the open transition without useEffect (lint blocks
  // setState-in-effect), the same way the edit dialog does it.
  const [draft, setDraft] = useState<{
    wasOpen: boolean;
    memberIds: string[];
    startedAt: string;
    endedAt: string;
    customStatus: string;
  }>({
    wasOpen: false,
    memberIds: [],
    startedAt: "",
    endedAt: "",
    customStatus: "",
  });

  if (open && !draft.wasOpen) {
    setDraft({
      wasOpen: true,
      memberIds: [],
      startedAt: "",
      endedAt: "",
      customStatus: "",
    });
    setError("");
  } else if (!open && draft.wasOpen) {
    setDraft((d) => ({ ...d, wasOpen: false }));
  }

  function handleSave() {
    if (!draft.startedAt) {
      setError("A start time is required.");
      return;
    }
    if (!draft.endedAt) {
      setError(
        "An end time is required. To start a front that is still going, " +
          "use Start front instead.",
      );
      return;
    }
    if (new Date(draft.endedAt) < new Date(draft.startedAt)) {
      setError("The end time can't be before the start time.");
      return;
    }
    const started = fromDateTimeLocal(draft.startedAt);
    const ended = fromDateTimeLocal(draft.endedAt);
    if (!started || !ended) {
      setError("That date doesn't look like a real one.");
      return;
    }
    setError("");

    createFront.mutate(
      {
        member_ids: draft.memberIds,
        started_at: started,
        ended_at: ended,
        custom_status: draft.customStatus.trim() || null,
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          onSaved?.();
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a past front</DialogTitle>
          <DialogDescription>
            Record a front that already happened. It goes straight into your
            history and does not change who is fronting now. Overlap with
            other entries is allowed.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <Label className="text-sm font-normal">Fronting members</Label>
          <MemberSelect
            selected={draft.memberIds}
            onChange={(m) => setDraft((d) => ({ ...d, memberIds: m }))}
            className="py-2"
            showGroupFilter
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-2">
            <Label htmlFor="entry-started-at" className="text-sm font-normal">
              Started
            </Label>
            <Input
              id="entry-started-at"
              type="datetime-local"
              value={draft.startedAt}
              onChange={(e) =>
                setDraft((d) => ({ ...d, startedAt: e.target.value }))
              }
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="entry-ended-at" className="text-sm font-normal">
              Ended
            </Label>
            <Input
              id="entry-ended-at"
              type="datetime-local"
              value={draft.endedAt}
              onChange={(e) =>
                setDraft((d) => ({ ...d, endedAt: e.target.value }))
              }
            />
          </div>
        </div>

        <p className="text-xs text-muted-foreground">
          Times are in {timeZone ?? "your device's local timezone"}. The
          picker's date format follows your browser.
        </p>

        <div className="space-y-2">
          <Label htmlFor="entry-custom-status" className="text-sm font-normal">
            Custom status
          </Label>
          <Input
            id="entry-custom-status"
            value={draft.customStatus}
            onChange={(e) =>
              setDraft((d) => ({ ...d, customStatus: e.target.value }))
            }
            placeholder="e.g. during a job interview"
            maxLength={500}
          />
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <DialogFooter>
          <Button
            onClick={handleSave}
            disabled={draft.memberIds.length === 0 || createFront.isPending}
          >
            {createFront.isPending ? "Adding..." : "Add entry"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
