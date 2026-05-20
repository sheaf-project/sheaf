import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { apiFetch } from "@/lib/api-client";
import { patchWebSettings } from "@/lib/client-settings";
import { ChevronDown, ChevronRight, History, Infinity as InfinityIcon, ListOrdered, Pencil } from "lucide-react";
import {
  useCurrentFronts,
  useFronts,
  useFrontsPaged,
  useUpdateFront,
  useDeleteFront,
} from "@/hooks/use-fronts";
import { useMembers } from "@/hooks/use-members";
import { PageHeader } from "@/components/page-header";
import { ColorDot } from "@/components/color-dot";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { EditFrontDialog } from "@/components/edit-front-dialog";
import { FrontAuditHistory } from "@/components/front-audit-history";
import { PageNav } from "@/components/page-nav";
import { StartFrontDialog } from "@/components/start-front-dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatDateTime, timeAgo } from "@/lib/utils";
import { getMySystem } from "@/lib/systems";
import type { Front } from "@/types/api";

const HISTORY_PAGE_SIZES = [25, 50, 100] as const;
type ViewMode = "infinite" | "paged";

interface FrontsViewPrefs {
  view?: ViewMode;
  pageSize?: number;
}

function isViewMode(v: unknown): v is ViewMode {
  return v === "infinite" || v === "paged";
}

function isPageSize(v: unknown): v is (typeof HISTORY_PAGE_SIZES)[number] {
  return (
    typeof v === "number" &&
    (HISTORY_PAGE_SIZES as readonly number[]).includes(v)
  );
}

export function FrontsPage() {
  const qc = useQueryClient();
  const { data: current, isLoading: currentLoading } = useCurrentFronts();

  // Saved defaults from client settings — only consulted when the URL
  // doesn't already pin a view / pageSize. URL wins so bookmarking and
  // link-sharing stay deterministic; settings just pick the default the
  // user sees on a fresh visit.
  const { data: clientSettings } = useQuery({
    queryKey: ["client-settings", "web"],
    queryFn: async () => {
      try {
        const res = await apiFetch<{ settings: Record<string, unknown> }>(
          "/v1/settings/client/web",
        );
        return res.settings;
      } catch {
        return {} as Record<string, unknown>;
      }
    },
    staleTime: 5 * 60 * 1000,
  });
  const savedFrontsPrefs =
    (clientSettings?.fronts as FrontsViewPrefs | undefined) ?? {};
  const savedView: ViewMode = isViewMode(savedFrontsPrefs.view)
    ? savedFrontsPrefs.view
    : "infinite";
  const savedPageSize = isPageSize(savedFrontsPrefs.pageSize)
    ? savedFrontsPrefs.pageSize
    : 50;

  const [searchParams, setSearchParams] = useSearchParams();
  const view: ViewMode = searchParams.has("view")
    ? searchParams.get("view") === "paged"
      ? "paged"
      : "infinite"
    : savedView;
  const page = Math.max(
    1,
    Number.parseInt(searchParams.get("page") || "1", 10) || 1,
  );
  const pageSize = (() => {
    if (!searchParams.has("pageSize")) return savedPageSize;
    const raw = Number.parseInt(searchParams.get("pageSize") || "50", 10);
    return isPageSize(raw) ? raw : savedPageSize;
  })();

  async function persistFrontsPrefs(next: FrontsViewPrefs) {
    // Atomic server-side merge of just our key — won't clobber a
    // concurrent write of some other client-settings key.
    try {
      await patchWebSettings({ fronts: { ...savedFrontsPrefs, ...next } });
      qc.invalidateQueries({ queryKey: ["client-settings", "web"] });
    } catch {
      // Persistence failure is non-fatal — URL state still works.
    }
  }

  function setView(next: ViewMode) {
    const params = new URLSearchParams(searchParams);
    if (next === "infinite") {
      params.delete("view");
      params.delete("page");
      params.delete("pageSize");
    } else {
      params.set("view", "paged");
      params.set("page", "1");
    }
    setSearchParams(params, { replace: true });
    void persistFrontsPrefs({ view: next });
  }
  function setPage(next: number) {
    const params = new URLSearchParams(searchParams);
    params.set("page", String(next));
    setSearchParams(params, { replace: true });
    // Page number is transient: not persisted.
  }
  function setPageSize(next: number) {
    const params = new URLSearchParams(searchParams);
    params.set("pageSize", String(next));
    params.set("page", "1");
    setSearchParams(params, { replace: true });
    if (isPageSize(next)) void persistFrontsPrefs({ pageSize: next });
  }

  const {
    items: infiniteHistory,
    isLoading: infiniteLoading,
    hasNextPage: infiniteHasMore,
    fetchNextPage: fetchMoreInfinite,
    isFetchingNextPage: infiniteLoadingMore,
  } = useFronts(50);
  const pagedQuery = useFrontsPaged(page, pageSize);
  const isPaged = view === "paged";
  const history = isPaged ? (pagedQuery.data?.items ?? []) : infiniteHistory;
  const historyLoading = isPaged ? pagedQuery.isLoading : infiniteLoading;
  const total = pagedQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const { data: members } = useMembers();
  const { data: system } = useQuery({ queryKey: ["system", "me"], queryFn: getMySystem });
  const updateFront = useUpdateFront();
  const deleteFront = useDeleteFront();
  const [showStart, setShowStart] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [editing, setEditing] = useState<Front | null>(null);
  const [expandedHistory, setExpandedHistory] = useState<Set<string>>(
    new Set(),
  );

  function toggleHistory(id: string) {
    setExpandedHistory((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

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
                  className="rounded-md border p-3 space-y-2"
                >
                  <div className="flex items-start justify-between gap-2">
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
                    <div className="flex items-center gap-1 shrink-0">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => toggleHistory(front.id)}
                        disabled={!front.has_audit_history}
                        aria-label="Show edit history"
                        title={
                          front.has_audit_history
                            ? "Show edit history"
                            : "No edit history"
                        }
                      >
                        {expandedHistory.has(front.id) ? (
                          <ChevronDown className="h-3.5 w-3.5" />
                        ) : (
                          <ChevronRight className="h-3.5 w-3.5" />
                        )}
                        <History className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setEditing(front)}
                      >
                        <Pencil className="h-3.5 w-3.5 mr-1" />
                        Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => handleEndFront(front.id)}
                        disabled={updateFront.isPending}
                      >
                        End
                      </Button>
                    </div>
                  </div>
                  {expandedHistory.has(front.id) && (
                    <FrontAuditHistory frontId={front.id} />
                  )}
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
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">History</h2>
        <div className="flex items-center gap-2 text-sm">
          {isPaged && total > 0 && (
            <span className="text-muted-foreground">
              {total} {total === 1 ? "entry" : "entries"}
            </span>
          )}
          {isPaged && (
            <Select
              value={String(pageSize)}
              onValueChange={(v) => setPageSize(Number.parseInt(v, 10))}
            >
              <SelectTrigger size="sm" className="h-8 w-[5.5rem]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {HISTORY_PAGE_SIZES.map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n} / page
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <div className="inline-flex rounded-md border bg-muted/40 p-0.5">
            <Button
              size="sm"
              variant={isPaged ? "ghost" : "default"}
              className="h-7 px-2"
              onClick={() => setView("infinite")}
              aria-pressed={!isPaged}
              title="Infinite scroll"
            >
              <InfinityIcon className="size-3.5" />
            </Button>
            <Button
              size="sm"
              variant={isPaged ? "default" : "ghost"}
              className="h-7 px-2"
              onClick={() => setView("paged")}
              aria-pressed={isPaged}
              title="Numbered pages"
            >
              <ListOrdered className="size-3.5" />
            </Button>
          </div>
        </div>
      </div>
      {historyLoading ? (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : history && history.length > 0 ? (
        <div className="space-y-2">
          {history.map((front) => (
            <div key={front.id} className="rounded-md border p-3 space-y-2">
              <div className="flex items-start justify-between gap-2">
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
                    className="h-7 px-2"
                    onClick={() => toggleHistory(front.id)}
                    disabled={!front.has_audit_history}
                    aria-label="Show edit history"
                    title={
                      front.has_audit_history
                        ? "Show edit history"
                        : "No edit history"
                    }
                  >
                    {expandedHistory.has(front.id) ? (
                      <ChevronDown className="h-3.5 w-3.5" />
                    ) : (
                      <ChevronRight className="h-3.5 w-3.5" />
                    )}
                    <History className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 px-2"
                    onClick={() => setEditing(front)}
                  >
                    <Pencil className="h-3.5 w-3.5 mr-1" />
                    Edit
                  </Button>
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
              {expandedHistory.has(front.id) && (
                <FrontAuditHistory frontId={front.id} />
              )}
            </div>
          ))}
          {!isPaged && infiniteHasMore && (
            <div className="flex justify-center pt-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => fetchMoreInfinite()}
                disabled={infiniteLoadingMore}
              >
                {infiniteLoadingMore ? "Loading…" : "Load older entries"}
              </Button>
            </div>
          )}
          {isPaged && (
            <div className="flex flex-col items-center gap-2 pt-2">
              <PageNav
                page={page}
                totalPages={totalPages}
                onChange={setPage}
              />
              <span className="text-xs text-muted-foreground">
                Page {page} of {totalPages}
              </span>
            </div>
          )}
        </div>
      ) : (
        <p className="text-muted-foreground">No front history yet.</p>
      )}

      <StartFrontDialog open={showStart} onOpenChange={setShowStart} />

      <EditFrontDialog
        front={editing}
        onOpenChange={(open) => !open && setEditing(null)}
      />

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
