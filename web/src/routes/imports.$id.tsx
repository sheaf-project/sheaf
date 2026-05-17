/**
 * Import job detail page — /imports/:id.
 *
 * Polls the job while it's non-terminal so an in-flight import shows
 * live counts, then settles into the final report: a summary card of
 * what was imported plus a per-record events table grouped by level.
 */
import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type ImportJobEvent,
  type ImportJobStatus,
  SOURCE_LABELS,
  deleteImportJob,
  getImportJob,
  isTerminal,
  pollWhileActive,
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

/** Render a counts dict as a labelled grid. Keys are snake_case from
 * the backend (members_imported, ...) — humanise them for display. */
function CountsGrid({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, v]) => v !== 0);
  if (entries.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Nothing imported yet.
      </p>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-2 text-sm">
      {entries.map(([key, value]) => (
        <div key={key}>
          {key.replace(/_/g, " ")}: <strong>{value.toLocaleString()}</strong>
        </div>
      ))}
    </div>
  );
}

const LEVEL_ORDER: Record<ImportJobEvent["level"], number> = {
  error: 0,
  warning: 1,
  info: 2,
};

function EventRow({ event }: { event: ImportJobEvent }) {
  const color =
    event.level === "error"
      ? "text-destructive"
      : event.level === "warning"
        ? "text-amber-600 dark:text-amber-500"
        : "text-muted-foreground";
  return (
    <div className="flex gap-2 border-b border-border/50 py-1.5 text-sm last:border-0">
      <span className={`w-16 shrink-0 font-medium ${color}`}>{event.level}</span>
      <span className="w-24 shrink-0 text-muted-foreground">{event.stage}</span>
      <span className="flex-1">
        {event.message}
        {event.record_ref ? (
          <span className="ml-1 text-muted-foreground">({event.record_ref})</span>
        ) : null}
      </span>
    </div>
  );
}

export function ImportDetailPage() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: job, isLoading, isError } = useQuery({
    queryKey: ["import-job", id],
    queryFn: () => getImportJob(id),
    refetchInterval: (query) => pollWhileActive(query.state.data?.status),
  });

  const mutate = useMutation({
    mutationFn: () => deleteImportJob(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["import-job", id] });
      qc.invalidateQueries({ queryKey: ["import-jobs"] });
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "Action failed"),
  });

  // Errors first, then warnings, then info — the user wants failures
  // up top, not buried under a wall of "imported member X" lines.
  const sortedEvents = useMemo(() => {
    if (!job) return [];
    return [...job.events].sort(
      (a, b) => LEVEL_ORDER[a.level] - LEVEL_ORDER[b.level],
    );
  }, [job]);

  if (isLoading) {
    return (
      <>
        <PageHeader title="Import" />
        <Skeleton className="h-48 max-w-2xl" />
      </>
    );
  }

  if (isError || !job) {
    return (
      <>
        <PageHeader title="Import" />
        <Card className="max-w-lg">
          <CardContent className="py-6 text-sm text-muted-foreground">
            Import not found. It may have been deleted, or the link is wrong.
            <div className="mt-4">
              <Button variant="outline" size="sm" asChild>
                <Link to="/imports">Back to imports</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      </>
    );
  }

  const terminal = isTerminal(job.status);
  const errorCount = job.events.filter((e) => e.level === "error").length;
  const warningCount = job.events.filter((e) => e.level === "warning").length;

  return (
    <>
      <PageHeader title={`Import — ${SOURCE_LABELS[job.source]}`}>
        <Button variant="outline" size="sm" asChild>
          <Link to="/imports">All imports</Link>
        </Button>
      </PageHeader>

      <div className="grid max-w-2xl gap-4">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-base">Status</CardTitle>
            <StatusBadge status={job.status} />
          </CardHeader>
          <CardContent className="space-y-3">
            {!terminal && (
              <p className="text-sm text-muted-foreground">
                {job.status === "pending"
                  ? "Queued — the importer will pick this up shortly."
                  : "Importing… this page updates automatically."}
              </p>
            )}
            {job.status === "failed" && job.last_error && (
              <p className="text-sm text-destructive">{job.last_error}</p>
            )}
            <CountsGrid counts={job.counts} />
            {terminal && (errorCount > 0 || warningCount > 0) && (
              <p className="text-sm text-muted-foreground">
                {errorCount > 0 && `${errorCount} error(s)`}
                {errorCount > 0 && warningCount > 0 && ", "}
                {warningCount > 0 && `${warningCount} warning(s)`}
                {" — see the report below."}
              </p>
            )}
            <div className="flex gap-2 pt-1">
              {job.status === "pending" && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => mutate.mutate()}
                  disabled={mutate.isPending}
                >
                  Cancel
                </Button>
              )}
              {terminal && !job.archived_at && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    mutate.mutate(undefined, {
                      onSuccess: () => navigate("/imports"),
                    })
                  }
                  disabled={mutate.isPending}
                >
                  Archive
                </Button>
              )}
              <Button variant="outline" size="sm" asChild>
                <Link to="/import">New import</Link>
              </Button>
            </div>
          </CardContent>
        </Card>

        {job.events.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Report ({job.events.length} event
                {job.events.length === 1 ? "" : "s"})
              </CardTitle>
            </CardHeader>
            <CardContent>
              {sortedEvents.map((event, i) => (
                <EventRow key={i} event={event} />
              ))}
            </CardContent>
          </Card>
        )}
      </div>
    </>
  );
}
