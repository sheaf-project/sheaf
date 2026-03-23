import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  useCurrentFronts,
  useFronts,
  useCreateFront,
  useUpdateFront,
  useDeleteFront,
} from "@/hooks/use-fronts";
import { useMembers } from "@/hooks/use-members";
import { PageHeader } from "@/components/page-header";
import { MemberSelect } from "@/components/member-select";
import { ColorDot } from "@/components/color-dot";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatDateTime } from "@/lib/utils";
import { getMySystem } from "@/lib/systems";

export function FrontsPage() {
  const { data: current, isLoading: currentLoading } = useCurrentFronts();
  const { data: history, isLoading: historyLoading } = useFronts();
  const { data: members } = useMembers();
  const { data: system } = useQuery({ queryKey: ["system", "me"], queryFn: getMySystem });
  const createFront = useCreateFront();
  const updateFront = useUpdateFront();
  const deleteFront = useDeleteFront();
  const [showStart, setShowStart] = useState(false);
  const [selectedMembers, setSelectedMembers] = useState<string[]>([]);
  const [replaceFronts, setReplaceFronts] = useState<boolean | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const memberMap = new Map(members?.map((m) => [m.id, m]) ?? []);

  // Initialise replaceFronts from system setting when dialog opens
  const effectiveReplace = replaceFronts ?? (system?.replace_fronts_default ?? true);

  function handleOpen() {
    setReplaceFronts(null); // reset to system default each time
    setSelectedMembers([]);
    setShowStart(true);
  }

  function handleStartFront() {
    if (selectedMembers.length === 0) return;
    createFront.mutate(
      { member_ids: selectedMembers, replace_fronts: effectiveReplace },
      {
        onSuccess: () => {
          setShowStart(false);
          setSelectedMembers([]);
        },
      },
    );
  }

  function handleEndFront(id: string) {
    updateFront.mutate({ id, data: { ended_at: new Date().toISOString() } });
  }

  function renderMembers(memberIds: string[]) {
    return memberIds.map((mid) => {
      const m = memberMap.get(mid);
      return (
        <Badge key={mid} variant="secondary" className="gap-1.5">
          <ColorDot color={m?.color ?? null} />
          {m?.display_name ?? m?.name ?? "Unknown"}
        </Badge>
      );
    });
  }

  return (
    <>
      <PageHeader title="Fronts">
        <Button onClick={handleOpen}>Start front</Button>
      </PageHeader>

      {/* Current */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="text-base">Currently fronting</CardTitle>
        </CardHeader>
        <CardContent>
          {currentLoading ? (
            <Skeleton className="h-12 w-full" />
          ) : current && current.length > 0 ? (
            <div className="space-y-3">
              {current.map((front) => (
                <div
                  key={front.id}
                  className="flex items-center justify-between rounded-md border p-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    {renderMembers(front.member_ids)}
                    <span className="text-xs text-muted-foreground">
                      since {formatDateTime(front.started_at)}
                    </span>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleEndFront(front.id)}
                    disabled={updateFront.isPending}
                  >
                    End
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Nobody is fronting.</p>
          )}
        </CardContent>
      </Card>

      <Separator className="my-6" />

      {/* History */}
      <h2 className="text-lg font-semibold mb-4">History</h2>
      {historyLoading ? (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : history && history.length > 0 ? (
        <div className="space-y-2">
          {history.map((front) => (
            <div
              key={front.id}
              className="flex items-center justify-between rounded-md border p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                {renderMembers(front.member_ids)}
              </div>
              <div className="flex items-center gap-3 text-sm text-muted-foreground shrink-0">
                <span>
                  {formatDateTime(front.started_at)}
                  {front.ended_at
                    ? ` — ${formatDateTime(front.ended_at)}`
                    : " — ongoing"}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-destructive-foreground h-7 px-2"
                  onClick={() => setDeleting(front.id)}
                >
                  Delete
                </Button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-muted-foreground">No front history yet.</p>
      )}

      {/* Start dialog */}
      <Dialog open={showStart} onOpenChange={setShowStart}>
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
          />
          <div className="flex items-center gap-2 pt-1">
            <Checkbox
              id="replace-fronts"
              checked={effectiveReplace}
              onCheckedChange={(v) => setReplaceFronts(v === true)}
            />
            <Label htmlFor="replace-fronts" className="text-sm font-normal cursor-pointer">
              End all current fronts
            </Label>
          </div>
          <DialogFooter>
            <Button
              onClick={handleStartFront}
              disabled={selectedMembers.length === 0 || createFront.isPending}
            >
              {createFront.isPending ? "Starting..." : "Start"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirm */}
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(open) => !open && setDeleting(null)}
        title="Delete front entry"
        description="Are you sure? This removes this front from history."
        onConfirm={() =>
          deleting &&
          deleteFront.mutate(deleting, {
            onSuccess: () => setDeleting(null),
          })
        }
        loading={deleteFront.isPending}
      />
    </>
  );
}
