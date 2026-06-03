import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { Plus, Trash2, Vote } from "lucide-react";
import { toast } from "sonner";
import { showApiErrorToast } from "@/lib/api-errors";

import { getMySystem } from "@/lib/systems";
import {
  createPoll,
  deletePoll,
  getPollServerConfig,
  listPolls,
} from "@/lib/polls";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { PageHeader } from "@/components/page-header";
import { PendingDeleteBadge } from "@/components/pending-delete-badge";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { isDeleteQueued } from "@/types/api";
import type {
  DestructiveConfirm,
  Poll,
  PollKind,
  PollResultsVisibility,
} from "@/types/api";

function offsetLocal(msFromNow: number): string {
  const d = new Date(Date.now() + msFromNow);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

function defaultClosesAtLocal(): string {
  // 24h from now, in datetime-local format the <input> needs.
  return offsetLocal(24 * 60 * 60 * 1000);
}

function formatRelative(iso: string): string {
  const target = new Date(iso).getTime();
  const now = Date.now();
  const diff = target - now;
  const abs = Math.abs(diff);
  const mins = Math.floor(abs / 60000);
  const hours = Math.floor(mins / 60);
  const days = Math.floor(hours / 24);
  const tense = diff >= 0 ? "in" : "ago";
  let unit;
  if (days >= 1) unit = `${days}d`;
  else if (hours >= 1) unit = `${hours}h`;
  else if (mins >= 1) unit = `${mins}m`;
  else unit = "<1m";
  return diff >= 0 ? `${tense} ${unit}` : `${unit} ${tense}`;
}

export function PollsPage() {
  const qc = useQueryClient();
  const { data: polls, isLoading } = useQuery({
    queryKey: ["polls"],
    queryFn: listPolls,
  });
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const { data: serverConfig } = useQuery({
    queryKey: ["polls", "server-config"],
    queryFn: getPollServerConfig,
  });

  const openCount = (polls ?? []).filter((p) => !p.is_closed).length;
  const concurrentCap = serverConfig?.max_concurrent_open_polls ?? 0;
  const atConcurrentCap = concurrentCap > 0 && openCount >= concurrentCap;

  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Poll | null>(null);

  const deleteMut = useMutation({
    mutationFn: ({
      id,
      confirm,
    }: {
      id: string;
      confirm?: DestructiveConfirm;
    }) => deletePoll(id, confirm),
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ["polls"] });
      setDeleting(null);
      if (isDeleteQueued(resp)) {
        toast.success(
          `Deletion queued. Will finalize after ${new Date(
            resp.finalize_after,
          ).toLocaleString()} unless cancelled.`,
        );
      } else {
        toast.success("Poll deleted");
      }
    },
  });

  return (
    <>
      <PageHeader title="Polls">
        <Button onClick={() => setCreating(true)} disabled={atConcurrentCap}>
          <Plus className="size-4" />
          New poll
        </Button>
      </PageHeader>
      {concurrentCap > 0 ? (
        <p className="mb-3 text-xs text-muted-foreground">
          {openCount} of {concurrentCap} open polls used on your tier.
          {atConcurrentCap
            ? " Wait for one to close, delete one, or upgrade for a larger cap."
            : ""}
        </p>
      ) : null}

      {isLoading ? (
        <div className="grid gap-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      ) : !polls || polls.length === 0 ? (
        <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
          <Vote className="mx-auto mb-2 size-6 opacity-50" />
          <p>No polls yet. Polls let your headmates vote on a question.</p>
          <p className="mt-1">
            Each vote is attributed to a fronting member, and every action is
            recorded in the audit log.
          </p>
        </div>
      ) : (
        <div className="grid gap-3">
          {polls.map((poll) => (
            <PollSummaryCard
              key={poll.id}
              poll={poll}
              onDelete={() => setDeleting(poll)}
            />
          ))}
        </div>
      )}

      {creating && (
        <CreatePollDialog
          onClose={() => setCreating(false)}
          serverConfig={serverConfig ?? null}
        />
      )}

      <DestructiveConfirmDialog
        open={Boolean(deleting)}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title="Delete poll?"
        description={
          deleting
            ? `Permanently delete "${deleting.question}". The audit log goes with it.`
            : ""
        }
        tier={system?.delete_confirmation ?? "none"}
        onConfirm={(confirm) => {
          if (deleting) deleteMut.mutate({ id: deleting.id, confirm });
        }}
        loading={deleteMut.isPending}
      />
    </>
  );
}

function PollSummaryCard({
  poll,
  onDelete,
}: {
  poll: Poll;
  onDelete: () => void;
}) {
  return (
    <div
      className={cn(
        "rounded-md border p-4",
        poll.pending_delete_at && "opacity-60",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <Link
            to={`/polls/${poll.id}`}
            className="font-medium hover:underline"
          >
            {poll.question}
          </Link>
          {poll.description ? (
            <p className="mt-1 text-sm text-muted-foreground line-clamp-2">
              {poll.description}
            </p>
          ) : null}
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="outline">
              {poll.kind === "single_choice" ? "Single choice" : "Multi choice"}
            </Badge>
            <Badge variant="outline">
              {poll.results_visibility === "live"
                ? "Live results"
                : "Results at close"}
            </Badge>
            {poll.is_closed ? (
              <Badge variant="secondary">Closed</Badge>
            ) : (
              <span>Closes {formatRelative(poll.closes_at)}</span>
            )}
            <span>
              {poll.total_votes} {poll.total_votes === 1 ? "vote" : "votes"}
            </span>
          </div>
          <PendingDeleteBadge
            finalizeAt={poll.pending_delete_at}
            className="mt-2"
          />
        </div>
        <Button
          size="icon"
          variant="ghost"
          aria-label="Delete poll"
          onClick={onDelete}
        >
          <Trash2 className="size-4" />
        </Button>
      </div>
    </div>
  );
}

function CreatePollDialog({
  onClose,
  serverConfig,
}: {
  onClose: () => void;
  serverConfig: import("@/types/api").PollServerConfig | null;
}) {
  const qc = useQueryClient();
  const [question, setQuestion] = useState("");
  const [description, setDescription] = useState("");
  const [kind, setKind] = useState<PollKind>("single_choice");
  const [visibility, setVisibility] = useState<PollResultsVisibility>("live");
  const [closesAtLocal, setClosesAtLocal] = useState(defaultClosesAtLocal());
  const [includeCustomFronts, setIncludeCustomFronts] = useState(false);
  const [retentionDays, setRetentionDays] = useState<number>(
    serverConfig?.default_retention_days ?? 30,
  );
  const [options, setOptions] = useState<string[]>(["", ""]);

  const maxRetention = serverConfig?.max_retention_days ?? 0;
  const maxClose = serverConfig?.max_close_seconds ?? 0;
  // datetime-local max attribute (only meaningful when there's a tier cap).
  // Computed once at dialog mount; the cap is a per-tier ceiling, not a
  // ticking clock, so a stale-by-a-few-seconds value is fine.
  const [closesAtMaxLocal] = useState<string | undefined>(() =>
    maxClose ? offsetLocal(maxClose * 1000) : undefined,
  );

  const createMut = useMutation({
    mutationFn: createPoll,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["polls"] });
      toast.success("Poll created");
      onClose();
    },
    onError: (err) => showApiErrorToast(err),
  });

  const trimmedOptions = options.map((o) => o.trim()).filter(Boolean);
  const valid =
    question.trim().length > 0 &&
    trimmedOptions.length >= 2 &&
    new Set(trimmedOptions.map((s) => s.toLowerCase())).size ===
      trimmedOptions.length;

  function submit() {
    if (!valid) return;
    const closesAt = new Date(closesAtLocal).toISOString();
    createMut.mutate({
      question: question.trim(),
      description: description.trim() || null,
      kind,
      results_visibility: visibility,
      closes_at: closesAt,
      include_custom_fronts: includeCustomFronts,
      retention_days: retentionDays,
      options: trimmedOptions.map((text) => ({ text })),
    });
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>New poll</DialogTitle>
          <DialogDescription>
            Polls run until their deadline, then auto-purge after a retention
            window. Headmates vote attributed to a fronting member; every
            action lands in the audit log.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="grid gap-2">
            <Label htmlFor="poll-q">Question</Label>
            <Input
              id="poll-q"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="What should we have for dinner?"
              maxLength={500}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="poll-desc">Description (optional)</Label>
            <Input
              id="poll-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={2000}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-2">
              <Label htmlFor="poll-kind">Kind</Label>
              <Select
                value={kind}
                onValueChange={(v) => setKind(v as PollKind)}
              >
                <SelectTrigger id="poll-kind">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="single_choice">Single choice</SelectItem>
                  <SelectItem value="multi_choice">Multi choice</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="poll-visibility">Results visibility</Label>
              <Select
                value={visibility}
                onValueChange={(v) =>
                  setVisibility(v as PollResultsVisibility)
                }
              >
                <SelectTrigger id="poll-visibility">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="live">Live</SelectItem>
                  <SelectItem value="end_only">Only after close</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="poll-closes">Closes at</Label>
            <Input
              id="poll-closes"
              type="datetime-local"
              value={closesAtLocal}
              onChange={(e) => setClosesAtLocal(e.target.value)}
              max={closesAtMaxLocal}
            />
            <p className="text-xs text-muted-foreground">
              {maxClose > 0
                ? `Up to ${Math.floor(maxClose / 86400)} days for your account tier.`
                : "Cannot be changed after creation."}
              {" "}Manual close isn't supported.
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="poll-retention">Retention after close (days)</Label>
            <Input
              id="poll-retention"
              type="number"
              min={1}
              max={maxRetention > 0 ? maxRetention : undefined}
              value={retentionDays}
              onChange={(e) =>
                setRetentionDays(
                  Math.max(1, Number.parseInt(e.target.value, 10) || 1),
                )
              }
            />
            <p className="text-xs text-muted-foreground">
              {maxRetention > 0
                ? `Up to ${maxRetention} days for your account tier.`
                : "No upper bound on this deployment."}
              {" "}After the close + retention window, the poll, votes, and
              audit log are deleted together.
            </p>
          </div>

          <label className="flex items-start gap-2 rounded-md border p-3 cursor-pointer hover:bg-accent">
            <Checkbox
              checked={includeCustomFronts}
              onCheckedChange={(v) => setIncludeCustomFronts(v === true)}
            />
            <div className="grid gap-1">
              <span className="text-sm font-medium">
                Allow votes from custom fronts
              </span>
              <span className="text-xs text-muted-foreground">
                Custom fronts (Asleep, Away, etc.) are normally system states,
                not voters. Off by default.
              </span>
            </div>
          </label>

          <div className="grid gap-2">
            <Label>Options</Label>
            {options.map((opt, i) => (
              <div key={i} className="flex gap-2">
                <Input
                  value={opt}
                  onChange={(e) => {
                    const next = [...options];
                    next[i] = e.target.value;
                    setOptions(next);
                  }}
                  placeholder={`Option ${i + 1}`}
                  maxLength={200}
                />
                {options.length > 2 ? (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Remove option"
                    onClick={() =>
                      setOptions(options.filter((_, j) => j !== i))
                    }
                  >
                    <Trash2 className="size-4" />
                  </Button>
                ) : null}
              </div>
            ))}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setOptions([...options, ""])}
              disabled={options.length >= 20}
            >
              <Plus className="size-4" />
              Add option
            </Button>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={!valid || createMut.isPending}
          >
            Create poll
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
