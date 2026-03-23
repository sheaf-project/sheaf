import { useState } from "react";
import { useCurrentFronts, useCreateFront, useUpdateFront } from "@/hooks/use-fronts";
import { useMembers } from "@/hooks/use-members";
import { PageHeader } from "@/components/page-header";
import { MemberSelect } from "@/components/member-select";
import { ColorDot } from "@/components/color-dot";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { timeAgo } from "@/lib/utils";

export function DashboardPage() {
  const { data: fronts, isLoading: frontsLoading } = useCurrentFronts();
  const { data: members } = useMembers();
  const createFront = useCreateFront();
  const updateFront = useUpdateFront();
  const [showStartFront, setShowStartFront] = useState(false);
  const [selectedMembers, setSelectedMembers] = useState<string[]>([]);

  const memberMap = new Map(members?.map((m) => [m.id, m]) ?? []);

  function handleStartFront() {
    if (selectedMembers.length === 0) return;
    createFront.mutate(
      { member_ids: selectedMembers },
      {
        onSuccess: () => {
          setShowStartFront(false);
          setSelectedMembers([]);
        },
      },
    );
  }

  function handleEndFront(id: string) {
    updateFront.mutate({
      id,
      data: { ended_at: new Date().toISOString() },
    });
  }

  return (
    <>
      <PageHeader title="Dashboard">
        <Button onClick={() => setShowStartFront(true)}>Start front</Button>
      </PageHeader>

      <div className="grid gap-6 md:grid-cols-2">
        {/* Current fronters */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Currently fronting</CardTitle>
          </CardHeader>
          <CardContent>
            {frontsLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
              </div>
            ) : fronts && fronts.length > 0 ? (
              <div className="space-y-3">
                {fronts.map((front) => (
                  <div
                    key={front.id}
                    className="flex items-center justify-between rounded-md border p-3"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      {front.member_ids.map((mid) => {
                        const m = memberMap.get(mid);
                        return (
                          <Badge key={mid} variant="secondary" className="gap-1.5">
                            <ColorDot color={m?.color ?? null} />
                            {m?.display_name ?? m?.name ?? "Unknown"}
                          </Badge>
                        );
                      })}
                      <span className="text-xs text-muted-foreground">
                        {timeAgo(front.started_at)}
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
              <p className="text-sm text-muted-foreground">Nobody is fronting right now.</p>
            )}
          </CardContent>
        </Card>

        {/* Quick stats */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">System</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-2xl font-semibold">{members?.length ?? 0}</p>
                <p className="text-sm text-muted-foreground">Members</p>
              </div>
              <div>
                <p className="text-2xl font-semibold">{fronts?.length ?? 0}</p>
                <p className="text-sm text-muted-foreground">Active fronts</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Dialog open={showStartFront} onOpenChange={setShowStartFront}>
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
    </>
  );
}
