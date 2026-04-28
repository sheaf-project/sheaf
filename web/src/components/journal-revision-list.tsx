import { Suspense, lazy, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { History, RotateCcw, Eye } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { listRevisions, restoreRevision } from "@/lib/journals";
import { formatDateTime } from "@/lib/date-format";
import type { ContentRevision, DateFormat } from "@/types/api";

const MarkdownPreview = lazy(() =>
  import("@/components/bio-editor").then((m) => ({
    default: m.MarkdownPreview,
  })),
);

export function JournalRevisionList({
  entryId,
  dateFormat = "ymd",
}: {
  entryId: string;
  dateFormat?: DateFormat;
}) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["journal", entryId, "revisions"],
    queryFn: () => listRevisions(entryId),
  });

  const [previewing, setPreviewing] = useState<ContentRevision | null>(null);

  const restore = useMutation({
    mutationFn: (revisionId: string) => restoreRevision(entryId, revisionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["journal", entryId] });
      qc.invalidateQueries({ queryKey: ["journal", entryId, "revisions"] });
      toast.success("Restored");
    },
  });

  if (isLoading) {
    return (
      <p className="text-sm text-muted-foreground">Loading revisions…</p>
    );
  }
  if (!data || data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground italic">
        No revisions yet. Edits to this entry will appear here.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-sm font-medium">
        <History className="h-4 w-4" />
        Revisions ({data.length})
      </div>
      <div className="space-y-2">
        {data.map((rev) => (
          <div
            key={rev.id}
            className="flex flex-wrap items-start gap-3 rounded-md border px-3 py-2 text-sm"
          >
            <div className="min-w-0 flex-1 space-y-0.5">
              <p className="font-medium truncate">
                {rev.title || "(untitled)"}
              </p>
              <p className="text-xs text-muted-foreground">
                {formatDateTime(rev.created_at, dateFormat)}
                {rev.editor_member_names.length > 0
                  ? ` · ${rev.editor_member_names.join(", ")}`
                  : ""}
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => setPreviewing(rev)}
              >
                <Eye className="h-3 w-3 mr-1" />
                Preview
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => restore.mutate(rev.id)}
                disabled={restore.isPending && restore.variables === rev.id}
              >
                <RotateCcw className="h-3 w-3 mr-1" />
                {restore.isPending && restore.variables === rev.id
                  ? "Restoring…"
                  : "Restore"}
              </Button>
            </div>
          </div>
        ))}
      </div>
      <Dialog
        open={previewing !== null}
        onOpenChange={(open) => !open && setPreviewing(null)}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>
              {previewing?.title ||
                (previewing
                  ? `Revision from ${formatDateTime(previewing.created_at, dateFormat)}`
                  : "Revision")}
            </DialogTitle>
          </DialogHeader>
          {previewing && (
            <div className="rounded-md border bg-muted/30 px-3 py-2">
              <Suspense
                fallback={
                  <p className="text-sm text-muted-foreground">Loading…</p>
                }
              >
                <MarkdownPreview content={previewing.body} />
              </Suspense>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
