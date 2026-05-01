import { useState } from "react";
import { useCurrentFronts, useUpdateFront } from "@/hooks/use-fronts";
import { useMembers } from "@/hooks/use-members";
import { PageHeader } from "@/components/page-header";
import { ColorDot } from "@/components/color-dot";
import { StartFrontDialog } from "@/components/start-front-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { timeAgo } from "@/lib/utils";

export function DashboardPage() {
  const { data: fronts, isLoading: frontsLoading } = useCurrentFronts();
  const { data: members } = useMembers();
  const updateFront = useUpdateFront();
  const [showStartFront, setShowStartFront] = useState(false);

  const memberMap = new Map(members?.map((m) => [m.id, m]) ?? []);

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

      <StartFrontDialog open={showStartFront} onOpenChange={setShowStartFront} />
    </>
  );
}
