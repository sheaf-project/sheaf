/**
 * Import history list — /imports.
 *
 * Lists the user's past + in-flight imports, polling while any are
 * still active so a freshly-queued job animates toward done without a
 * manual refresh. Cursor-paginated via "Load more". Each row links to
 * the detail page.
 */
import { Link } from "react-router";
import { useInfiniteQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type ImportJobStatus,
  type ImportJobSummary,
  SOURCE_LABELS,
  listImportJobs,
} from "@/lib/imports";

function StatusBadge({ status }: { status: ImportJobStatus }) {
  const variant =
    status === "failed"
      ? "destructive"
      : status === "cancelled"
        ? "outline"
        : status === "complete"
          ? "default"
          : "secondary";
  return <Badge variant={variant}>{status}</Badge>;
}

/** Compact "12 members, 3 groups" summary from a counts dict.
 *
 * Includes every non-zero counter, not just the *_imported ones — a
 * job whose only counts are failures (e.g. members_failed) still has
 * something worth showing in the list rather than a bare "—". */
function countsSummary(counts: Record<string, number>): string {
  const parts = Object.entries(counts)
    .filter(([, v]) => v > 0)
    .map(
      ([key, v]) =>
        `${v} ${key.replace(/_imported$/, "").replace(/_/g, " ")}`,
    );
  return parts.length > 0 ? parts.join(", ") : "—";
}

function ImportRow({ job }: { job: ImportJobSummary }) {
  return (
    <Link
      to={`/imports/${job.id}`}
      className="flex items-center gap-3 border-b border-border/50 px-4 py-3 text-sm transition-colors last:border-0 hover:bg-muted/50"
    >
      <span className="w-40 shrink-0 font-medium">
        {SOURCE_LABELS[job.source]}
      </span>
      <StatusBadge status={job.status} />
      <span className="flex-1 truncate text-muted-foreground">
        {countsSummary(job.counts)}
      </span>
      <span className="shrink-0 text-xs text-muted-foreground">
        {new Date(job.created_at).toLocaleString()}
      </span>
    </Link>
  );
}

export function ImportsPage() {
  const {
    data,
    isLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["import-jobs"],
    queryFn: ({ pageParam }) => listImportJobs({ cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    refetchInterval: (query) => {
      // Poll while any loaded job is still in flight. Refetch covers
      // every loaded page, but pages are small so that's cheap.
      const pages = query.state.data?.pages ?? [];
      const active = pages.some((p) =>
        p.items.some(
          (j) => j.status === "pending" || j.status === "running",
        ),
      );
      return active ? 2000 : false;
    },
  });

  const items = data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <PageHeader title="Imports">
        <Button size="sm" asChild>
          <Link to="/import">New import</Link>
        </Button>
      </PageHeader>

      {isLoading ? (
        <Skeleton className="h-40 max-w-3xl" />
      ) : items.length === 0 ? (
        <Card className="max-w-lg">
          <CardContent className="py-6 text-sm text-muted-foreground">
            No imports yet. Bring in data from PluralKit, SimplyPlural,
            Tupperbox, or a Sheaf export.
            <div className="mt-4">
              <Button size="sm" asChild>
                <Link to="/import">Start an import</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="max-w-3xl space-y-3">
          <Card>
            <CardContent className="px-0 py-0">
              {items.map((job) => (
                <ImportRow key={job.id} job={job} />
              ))}
            </CardContent>
          </Card>
          {hasNextPage && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => fetchNextPage()}
              disabled={isFetchingNextPage}
            >
              {isFetchingNextPage ? "Loading…" : "Load older imports"}
            </Button>
          )}
        </div>
      )}
    </>
  );
}
