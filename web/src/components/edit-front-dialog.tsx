import { useState } from "react";

import { useDateFormatters } from "@/hooks/use-date-formatters";
import { useUpdateFront } from "@/hooks/use-fronts";
import type { Front } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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

export function EditFrontDialog({
  front,
  onOpenChange,
  onSaved,
}: {
  // null = closed
  front: Front | null;
  onOpenChange: (open: boolean) => void;
  onSaved?: () => void;
}) {
  const updateFront = useUpdateFront();
  // datetime-local values are rendered/parsed in the display timezone, so the
  // times a user reads elsewhere match the times they edit here.
  const { toDateTimeLocal, fromDateTimeLocal, timeZone } = useDateFormatters();
  const [error, setError] = useState("");

  // Reinit the form on the open transition without useEffect (lint
  // blocks setState-in-effect). Tracking which front the form was
  // last populated from is enough.
  const [draft, setDraft] = useState<{
    fromFrontId: string | null;
    memberIds: string[];
    startedAt: string;
    endedAt: string;
    reopen: boolean;
    customStatus: string;
  }>({
    fromFrontId: null,
    memberIds: [],
    startedAt: "",
    endedAt: "",
    reopen: false,
    customStatus: "",
  });

  if (front && draft.fromFrontId !== front.id) {
    setDraft({
      fromFrontId: front.id,
      memberIds: front.member_ids,
      startedAt: toDateTimeLocal(front.started_at),
      endedAt: toDateTimeLocal(front.ended_at),
      reopen: false,
      customStatus: front.custom_status ?? "",
    });
    setError("");
  } else if (!front && draft.fromFrontId !== null) {
    setDraft((d) => ({ ...d, fromFrontId: null }));
  }

  function handleSave() {
    if (!front) return;

    // Validate the *effective* resulting times before sending. A
    // datetime-local that the browser couldn't represent (e.g. an
    // impossible date like 31 April) comes through empty; without this
    // check the empty start would be silently omitted from the PATCH
    // while ended_at still goes, leaving an end before the unchanged
    // start that the server rejects with a generic 400. Catch it here
    // with a precise message instead.
    if (!draft.startedAt) {
      setError("A start time is required.");
      return;
    }
    const effEnded = draft.reopen ? "" : draft.endedAt;
    if (effEnded && new Date(effEnded) < new Date(draft.startedAt)) {
      setError("The end time can't be before the start time.");
      return;
    }
    setError("");

    const body: {
      started_at?: string;
      ended_at?: string | null;
      member_ids?: string[];
      custom_status?: string | null;
    } = {};

    const originalStartedAt = toDateTimeLocal(front.started_at);
    const originalEndedAt = toDateTimeLocal(front.ended_at);
    const originalCustomStatus = front.custom_status ?? "";
    const originalMemberIds = [...front.member_ids].sort();
    const draftMemberIds = [...draft.memberIds].sort();

    if (draft.startedAt !== originalStartedAt) {
      const iso = fromDateTimeLocal(draft.startedAt);
      if (iso) body.started_at = iso;
    }
    // Reopen flag wins: explicit clear.
    if (draft.reopen) {
      body.ended_at = null;
    } else if (draft.endedAt !== originalEndedAt) {
      body.ended_at = fromDateTimeLocal(draft.endedAt);
    }
    if (draft.customStatus !== originalCustomStatus) {
      body.custom_status = draft.customStatus.trim() || null;
    }
    if (
      originalMemberIds.length !== draftMemberIds.length ||
      originalMemberIds.some((id, i) => id !== draftMemberIds[i])
    ) {
      body.member_ids = draft.memberIds;
    }

    if (Object.keys(body).length === 0) {
      onOpenChange(false);
      return;
    }

    updateFront.mutate(
      { id: front.id, data: body },
      {
        onSuccess: () => {
          onOpenChange(false);
          onSaved?.();
        },
      },
    );
  }

  return (
    <Dialog open={!!front} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit front entry</DialogTitle>
          <DialogDescription>
            Changes are recorded in this entry's history. Overlap with
            adjacent entries is allowed; ended_at before started_at is not.
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
            <Label htmlFor="started-at" className="text-sm font-normal">
              Started
            </Label>
            <Input
              id="started-at"
              type="datetime-local"
              value={draft.startedAt}
              onChange={(e) =>
                setDraft((d) => ({ ...d, startedAt: e.target.value }))
              }
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ended-at" className="text-sm font-normal">
              Ended
            </Label>
            <Input
              id="ended-at"
              type="datetime-local"
              value={draft.endedAt}
              onChange={(e) =>
                setDraft((d) => ({ ...d, endedAt: e.target.value, reopen: false }))
              }
              disabled={draft.reopen}
            />
          </div>
        </div>

        <p className="text-xs text-muted-foreground">
          Times are in {timeZone ?? "your device's local timezone"}. The
          picker's date format follows your browser.
        </p>

        {front?.ended_at && (
          <div className="flex items-center gap-2">
            <Checkbox
              id="reopen"
              checked={draft.reopen}
              onCheckedChange={(v) =>
                setDraft((d) => ({
                  ...d,
                  reopen: v === true,
                  endedAt: v === true ? "" : d.endedAt,
                }))
              }
            />
            <Label
              htmlFor="reopen"
              className="text-sm font-normal cursor-pointer"
            >
              Reopen (clear ended_at)
            </Label>
          </div>
        )}

        <div className="space-y-2">
          <Label htmlFor="custom-status" className="text-sm font-normal">
            Custom status
          </Label>
          <Input
            id="custom-status"
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
            disabled={
              !front ||
              draft.memberIds.length === 0 ||
              updateFront.isPending
            }
          >
            {updateFront.isPending ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
