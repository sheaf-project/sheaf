import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  useCurrentFronts,
  useFronts,
  useUpdateFront,
  useDeleteFront,
} from "@/hooks/use-fronts";
import { useMembers } from "@/hooks/use-members";
import { PageHeader } from "@/components/page-header";
import { ColorDot } from "@/components/color-dot";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { StartFrontDialog } from "@/components/start-front-dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { formatDateTime, timeAgo } from "@/lib/utils";
import { getMySystem } from "@/lib/systems";

export function FrontsPage() {
  const { data: current, isLoading: currentLoading } = useCurrentFronts();
  const { data: history, isLoading: historyLoading } = useFronts();
  const { data: members } = useMembers();
  const { data: system } = useQuery({ queryKey: ["system", "me"], queryFn: getMySystem });
  const updateFront = useUpdateFront();
  const deleteFront = useDeleteFront();
  const [showStart, setShowStart] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  const memberMap = new Map(members?.map((m) => [m.id, m]) ?? []);

  function handleEndFront(id: string) {
    updateFront.mutate({ id, data: { ended_at: new Date().toISOString() } });
  }

  function renderMembers(
    memberIds: string[],
    memberSince?: Record<string, string>,
    cappedIds?: string[],
  ) {
    return memberIds.map((mid) => {
      const m = memberMap.get(mid);
      const since = memberSince?.[mid];
      const capped = cappedIds?.includes(mid) ?? false;
      return (
        <Badge key={mid} variant="secondary" className="gap-1.5">
          <ColorDot color={m?.color ?? null} />
          {m?.emoji && <span>{m.emoji}</span>}
          {m?.display_name ?? m?.name ?? "Unknown"}
          {since && (
            <span className="text-muted-foreground">
              · {capped ? "> " : ""}{timeAgo(since)}
            </span>
          )}
        </Badge>
      );
    });
  }

  return (
    <>
      <PageHeader title="Fronts">
        <Button onClick={() => setShowStart(true)}>Start front</Button>
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
                  className="flex items-start justify-between rounded-md border p-3 gap-2"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {renderMembers(
                        front.member_ids,
                        front.member_since,
                        front.member_since_capped,
                      )}
                    </div>
                    {front.custom_status && (
                      <p className="mt-2 text-sm italic text-muted-foreground">
                        &ldquo;{front.custom_status}&rdquo;
                      </p>
                    )}
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
              className="flex items-start justify-between rounded-md border p-3 gap-2"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  {renderMembers(front.member_ids)}
                </div>
                {front.custom_status && (
                  <p className="mt-2 text-sm italic text-muted-foreground">
                    &ldquo;{front.custom_status}&rdquo;
                  </p>
                )}
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

      <StartFrontDialog open={showStart} onOpenChange={setShowStart} />

      {/* Delete confirm */}
      <DestructiveConfirmDialog
        open={!!deleting}
        onOpenChange={(open) => !open && setDeleting(null)}
        title="Delete front entry"
        description="Are you sure? This removes this front from history."
        tier={system?.delete_confirmation ?? "none"}
        onConfirm={(confirm) =>
          deleting &&
          deleteFront.mutate(
            { id: deleting, confirm },
            { onSuccess: () => setDeleting(null) },
          )
        }
        loading={deleteFront.isPending}
      />
    </>
  );
}
