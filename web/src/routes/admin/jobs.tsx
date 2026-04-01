import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  getJobs,
  triggerJob,
  getJobLogs,
  type JobInfo,
  type JobLogEntry,
} from "@/lib/admin";
import { timeAgo } from "@/lib/utils";
import {
  Play,
  CheckCircle2,
  XCircle,
  Clock,
  ChevronDown,
  ChevronRight,
} from "lucide-react";

function formatDuration(ms: number | null): string {
  if (ms === null) return "-";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatInterval(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}


function StatusBadge({ status }: { status: string }) {
  if (status === "success") {
    return (
      <Badge variant="outline" className="gap-1 text-green-600 border-green-600/30">
        <CheckCircle2 className="h-3 w-3" />
        Success
      </Badge>
    );
  }
  if (status === "error") {
    return (
      <Badge variant="outline" className="gap-1 text-destructive border-destructive/30">
        <XCircle className="h-3 w-3" />
        Error
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="gap-1">
      <Clock className="h-3 w-3" />
      Running
    </Badge>
  );
}

function LogRow({ log }: { log: JobLogEntry }) {
  const [showDetail, setShowDetail] = useState(false);
  const detail = log.error_message || log.details;
  const hasDetail = !!detail;

  return (
    <>
      <tr className="border-t border-border/50">
        <td className="py-1 text-muted-foreground">
          {timeAgo(log.started_at)}
        </td>
        <td className="py-1">
          <StatusBadge status={log.status} />
        </td>
        <td className="py-1 text-right">{log.items_processed}</td>
        <td className="py-1 text-right text-muted-foreground">
          {formatDuration(log.duration_ms)}
        </td>
        <td className="py-1 pl-3">
          {hasDetail ? (
            <button
              onClick={() => setShowDetail(!showDetail)}
              className="text-muted-foreground hover:text-foreground transition-colors underline decoration-dotted"
            >
              {showDetail ? "Hide" : "View details"}
            </button>
          ) : (
            <span className="text-muted-foreground">-</span>
          )}
        </td>
      </tr>
      {showDetail && detail && (
        <tr>
          <td colSpan={5} className="pb-2 pt-0">
            <pre className="text-xs text-muted-foreground bg-muted/50 rounded p-2 whitespace-pre-wrap break-all max-h-60 overflow-auto">
              {detail}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}

function JobRow({ job }: { job: JobInfo }) {
  const qc = useQueryClient();
  const [running, setRunning] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const { data: logs } = useQuery({
    queryKey: ["admin", "jobs", job.name, "logs"],
    queryFn: () => getJobLogs(job.name, 10),
    enabled: expanded,
  });

  async function handleRun() {
    setRunning(true);
    try {
      await triggerJob(job.name);
      await qc.invalidateQueries({ queryKey: ["admin", "jobs"] });
    } catch {
      // Error toast handled by apiFetch
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="border-b last:border-b-0">
      <div className="flex items-center gap-3 py-3 px-4">
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{job.name}</span>
            {!job.enabled && (
              <Badge variant="secondary" className="text-xs">
                Disabled
              </Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground">{job.description}</p>
        </div>
        <div className="text-xs text-muted-foreground text-right shrink-0 w-20">
          every {formatInterval(job.interval_seconds)}
        </div>
        <div className="shrink-0 w-32 text-right">
          {job.last_run ? (
            <div className="space-y-0.5">
              <StatusBadge status={job.last_run.status} />
              <p className="text-xs text-muted-foreground">
                {timeAgo(job.last_run.started_at)}
                {job.last_run.items_processed > 0 &&
                  ` · ${job.last_run.items_processed} items`}
              </p>
            </div>
          ) : (
            <span className="text-xs text-muted-foreground">Never run</span>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="shrink-0 h-7 w-7 p-0"
          onClick={handleRun}
          disabled={running}
          title="Run now"
        >
          <Play className="h-3.5 w-3.5" />
        </Button>
      </div>

      {expanded && logs && logs.length > 0 && (
        <div className="px-4 pb-3 pl-11">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground">
                <th className="text-left font-medium py-1">Time</th>
                <th className="text-left font-medium py-1">Status</th>
                <th className="text-right font-medium py-1">Items</th>
                <th className="text-right font-medium py-1">Duration</th>
                <th className="text-left font-medium py-1 pl-3">Details</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log: JobLogEntry) => (
                <LogRow key={log.id} log={log} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {expanded && logs && logs.length === 0 && (
        <p className="px-4 pb-3 pl-11 text-xs text-muted-foreground">
          No run history yet.
        </p>
      )}
    </div>
  );
}

export function AdminJobsPage() {
  const { data: jobs, isLoading } = useQuery({
    queryKey: ["admin", "jobs"],
    queryFn: getJobs,
  });

  return (
    <>
      <PageHeader title="Scheduled Jobs" />
      <div className="max-w-3xl">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Jobs</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {isLoading && (
              <p className="p-4 text-sm text-muted-foreground">Loading...</p>
            )}
            {jobs?.map((job) => <JobRow key={job.name} job={job} />)}
            {jobs && jobs.length === 0 && (
              <p className="p-4 text-sm text-muted-foreground">
                No jobs registered.
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
