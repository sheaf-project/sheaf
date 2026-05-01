import { Suspense, lazy, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { diffLines } from "diff";
import {
  History,
  RotateCcw,
  Eye,
  GitCompare,
  Pin,
  PinOff,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { formatDateTime } from "@/lib/date-format";
import type {
  ContentRevision,
  DateFormat,
  DeleteConfirmation,
  DestructiveConfirm,
  UnpinRevisionResponse,
} from "@/types/api";

const MarkdownPreview = lazy(() =>
  import("@/components/bio-editor").then((m) => ({
    default: m.MarkdownPreview,
  })),
);

function DiffView({ from, to }: { from: string; to: string }) {
  const parts = useMemo(() => diffLines(from, to), [from, to]);
  if (parts.length === 1 && !parts[0].added && !parts[0].removed) {
    return (
      <p className="text-sm text-muted-foreground italic">
        No differences — this revision matches the current content.
      </p>
    );
  }
  return (
    <pre className="max-h-[60vh] overflow-auto rounded-md border bg-muted/30 px-3 py-2 text-xs leading-relaxed whitespace-pre-wrap font-mono">
      {parts.map((p, i) => (
        <span
          key={i}
          className={
            p.added
              ? "block bg-green-500/15 text-green-900 dark:text-green-200"
              : p.removed
                ? "block bg-red-500/15 text-red-900 dark:text-red-200"
                : "block text-muted-foreground"
          }
        >
          {(p.added ? "+ " : p.removed ? "- " : "  ") + p.value}
        </span>
      ))}
    </pre>
  );
}

export function ContentRevisionList({
  targetId,
  currentBody,
  queryKey,
  list,
  restore,
  pin,
  unpin,
  safetyEnabled,
  authTier,
  invalidateOnRestore,
  emptyMessage = "No revisions yet. Edits will appear here.",
  dateFormat = "ymd",
}: {
  targetId: string;
  currentBody: string;
  queryKey: readonly unknown[];
  list: (id: string) => Promise<ContentRevision[]>;
  restore: (id: string, revisionId: string) => Promise<unknown>;
  pin?: (id: string, revisionId: string) => Promise<unknown>;
  unpin?: (
    id: string,
    revisionId: string,
    confirm?: DestructiveConfirm,
  ) => Promise<UnpinRevisionResponse>;
  /** When true, unpin requires re-auth and queues a PendingAction. */
  safetyEnabled?: boolean;
  /** Auth tier in force; only consulted when safetyEnabled. */
  authTier?: DeleteConfirmation;
  invalidateOnRestore: readonly (readonly unknown[])[];
  emptyMessage?: string;
  dateFormat?: DateFormat;
}) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => list(targetId),
  });

  const [previewing, setPreviewing] = useState<{
    rev: ContentRevision;
    tab: "preview" | "diff";
  } | null>(null);

  const [unpinConfirm, setUnpinConfirm] = useState<ContentRevision | null>(null);

  function invalidateAll() {
    for (const key of invalidateOnRestore) {
      qc.invalidateQueries({ queryKey: [...key] });
    }
  }

  const restoreMut = useMutation({
    mutationFn: (revisionId: string) => restore(targetId, revisionId),
    onSuccess: () => {
      invalidateAll();
      toast.success("Restored");
    },
  });

  const pinMut = useMutation({
    mutationFn: (revisionId: string) => pin!(targetId, revisionId),
    onSuccess: () => {
      invalidateAll();
      toast.success("Revision pinned");
    },
  });

  const unpinMut = useMutation({
    mutationFn: ({
      revisionId,
      confirm,
    }: {
      revisionId: string;
      confirm?: DestructiveConfirm;
    }) => unpin!(targetId, revisionId, confirm),
    onSuccess: (resp) => {
      invalidateAll();
      setUnpinConfirm(null);
      if (resp.pending_action_id && resp.finalize_after) {
        const when = new Date(resp.finalize_after).toLocaleString();
        toast.success(`Unpin queued — finalizes ${when}. Cancel from Safety settings.`);
      } else {
        toast.success("Revision unpinned");
      }
    },
  });

  // Sort: pinned first, then chronological (newest unpinned first).
  const sorted = useMemo(() => {
    if (!data) return [];
    return [...data].sort((a, b) => {
      if (a.pinned_at && !b.pinned_at) return -1;
      if (!a.pinned_at && b.pinned_at) return 1;
      return b.created_at.localeCompare(a.created_at);
    });
  }, [data]);

  const pinnedCount = data?.filter((r) => r.pinned_at).length ?? 0;

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading revisions…</p>;
  }
  if (!data || data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground italic">{emptyMessage}</p>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-sm font-medium">
        <History className="h-4 w-4" />
        Revisions ({data.length})
        {pinnedCount > 0 && (
          <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
            <Pin className="h-3 w-3 mr-1" />
            {pinnedCount} pinned
          </Badge>
        )}
      </div>
      <div className="space-y-2">
        {sorted.map((rev, idx) => {
          const isPinned = rev.pinned_at !== null;
          const showDivider =
            idx > 0 &&
            sorted[idx - 1].pinned_at !== null &&
            !isPinned;
          return (
            <div key={rev.id}>
              {showDivider && (
                <div className="my-2 border-t border-dashed" aria-hidden />
              )}
              <div
                className={
                  "flex flex-wrap items-start gap-3 rounded-md border px-3 py-2 text-sm " +
                  (isPinned ? "border-primary/40 bg-primary/5" : "")
                }
              >
                <div className="min-w-0 flex-1 space-y-0.5">
                  <p className="font-medium truncate flex items-center gap-2">
                    {isPinned && (
                      <Pin className="h-3 w-3 shrink-0 text-primary fill-primary" />
                    )}
                    <span className="truncate">{rev.title || "(untitled)"}</span>
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
                    onClick={() => setPreviewing({ rev, tab: "preview" })}
                  >
                    <Eye className="h-3 w-3 mr-1" />
                    Preview
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-xs"
                    onClick={() => setPreviewing({ rev, tab: "diff" })}
                  >
                    <GitCompare className="h-3 w-3 mr-1" />
                    Diff
                  </Button>
                  {pin && unpin && (
                    isPinned ? (
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 text-xs"
                        onClick={() => {
                          if (safetyEnabled) {
                            setUnpinConfirm(rev);
                          } else {
                            unpinMut.mutate({ revisionId: rev.id });
                          }
                        }}
                        disabled={
                          unpinMut.isPending &&
                          unpinMut.variables?.revisionId === rev.id
                        }
                      >
                        <PinOff className="h-3 w-3 mr-1" />
                        {unpinMut.isPending &&
                        unpinMut.variables?.revisionId === rev.id
                          ? "Unpinning…"
                          : "Unpin"}
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 text-xs"
                        onClick={() => pinMut.mutate(rev.id)}
                        disabled={
                          pinMut.isPending && pinMut.variables === rev.id
                        }
                      >
                        <Pin className="h-3 w-3 mr-1" />
                        {pinMut.isPending && pinMut.variables === rev.id
                          ? "Pinning…"
                          : "Pin"}
                      </Button>
                    )
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-xs"
                    onClick={() => restoreMut.mutate(rev.id)}
                    disabled={
                      restoreMut.isPending && restoreMut.variables === rev.id
                    }
                  >
                    <RotateCcw className="h-3 w-3 mr-1" />
                    {restoreMut.isPending && restoreMut.variables === rev.id
                      ? "Restoring…"
                      : "Restore"}
                  </Button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <DestructiveConfirmDialog
        open={unpinConfirm !== null}
        onOpenChange={(open) => !open && setUnpinConfirm(null)}
        title="Unpin revision?"
        description={
          "This pin protects the revision from automatic trim. " +
          "Unpinning will queue a pending action you can cancel from the Safety settings page."
        }
        tier={authTier ?? "none"}
        actionLabel="Queue unpin"
        actionLabelLoading="Queuing…"
        loading={unpinMut.isPending}
        onConfirm={(confirm) =>
          unpinConfirm && unpinMut.mutate({ revisionId: unpinConfirm.id, confirm })
        }
      />
      <Dialog
        open={previewing !== null}
        onOpenChange={(open) => !open && setPreviewing(null)}
      >
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {previewing?.rev.title ||
                (previewing
                  ? `Revision from ${formatDateTime(previewing.rev.created_at, dateFormat)}`
                  : "Revision")}
            </DialogTitle>
          </DialogHeader>
          {previewing && (
            <Tabs
              value={previewing.tab}
              onValueChange={(v) =>
                setPreviewing({
                  rev: previewing.rev,
                  tab: v as "preview" | "diff",
                })
              }
            >
              <TabsList>
                <TabsTrigger value="preview">Preview</TabsTrigger>
                <TabsTrigger value="diff">Diff vs current</TabsTrigger>
              </TabsList>
              <TabsContent value="preview" className="mt-2">
                <div className="rounded-md border bg-muted/30 px-3 py-2">
                  <Suspense
                    fallback={
                      <p className="text-sm text-muted-foreground">Loading…</p>
                    }
                  >
                    <MarkdownPreview content={previewing.rev.body} />
                  </Suspense>
                </div>
              </TabsContent>
              <TabsContent value="diff" className="mt-2">
                <DiffView from={previewing.rev.body} to={currentBody} />
              </TabsContent>
            </Tabs>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
