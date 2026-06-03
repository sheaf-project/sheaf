import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { showApiErrorToast } from "@/lib/api-errors";

import { useMembers } from "@/hooks/use-members";
import { getCurrentFronts } from "@/lib/fronts";
import { getMySystem } from "@/lib/systems";
import {
  castVote,
  deletePoll,
  getAudit,
  getPoll,
  withdrawVote,
} from "@/lib/polls";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { isDeleteQueued } from "@/types/api";
import type {
  DestructiveConfirm,
  Member,
  Poll,
  PollVoteEvent,
} from "@/types/api";

export function PollDetailPage() {
  const { pollId } = useParams<{ pollId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: poll, isLoading } = useQuery({
    queryKey: ["poll", pollId],
    queryFn: () => getPoll(pollId!),
    enabled: Boolean(pollId),
  });
  const { data: members } = useMembers();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const { data: currentFronts } = useQuery({
    queryKey: ["fronts", "current"],
    queryFn: getCurrentFronts,
  });
  const { data: audit } = useQuery({
    queryKey: ["poll", pollId, "audit"],
    queryFn: () => getAudit(pollId!),
    enabled: Boolean(pollId),
  });

  const [deleting, setDeleting] = useState(false);

  const memberById = useMemo(
    () => new Map<string, Member>((members ?? []).map((m) => [m.id, m])),
    [members],
  );
  const frontingIds = useMemo(() => {
    const ids = new Set<string>();
    for (const f of currentFronts ?? []) {
      for (const id of f.member_ids) ids.add(id);
    }
    return ids;
  }, [currentFronts]);

  const deleteMut = useMutation({
    mutationFn: ({ confirm }: { confirm?: DestructiveConfirm }) =>
      deletePoll(pollId!, confirm),
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ["polls"] });
      if (isDeleteQueued(resp)) {
        toast.success(
          `Deletion queued. Will finalize after ${new Date(
            resp.finalize_after,
          ).toLocaleString()} unless cancelled.`,
        );
      } else {
        toast.success("Poll deleted");
      }
      navigate("/polls");
    },
  });

  if (isLoading || !poll) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-32" />
      </div>
    );
  }

  return (
    <>
      <div className="mb-2">
        <Button variant="ghost" size="sm" asChild>
          <Link to="/polls">
            <ArrowLeft className="size-4" />
            Back to polls
          </Link>
        </Button>
      </div>
      <PageHeader title={poll.question}>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setDeleting(true)}
        >
          <Trash2 className="size-4" />
          Delete
        </Button>
      </PageHeader>

      <div className="space-y-4">
        <div className="rounded-md border p-4">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="outline">
              {poll.kind === "single_choice" ? "Single choice" : "Multi choice"}
            </Badge>
            <Badge variant="outline">
              {poll.results_visibility === "live"
                ? "Live results"
                : "Results at close"}
            </Badge>
            <Badge variant="outline">
              {poll.restrict_voting_to_fronters
                ? "Fronters only"
                : "Open to all members"}
            </Badge>
            {poll.is_closed ? (
              <Badge variant="secondary">Closed</Badge>
            ) : (
              <span>
                Closes {new Date(poll.closes_at).toLocaleString()}
              </span>
            )}
            <span>
              Auto-purges {new Date(poll.purges_at).toLocaleDateString()} (
              {poll.retention_days}d after close)
            </span>
          </div>
          {poll.description ? (
            <p className="mt-3 whitespace-pre-wrap text-sm">
              {poll.description}
            </p>
          ) : null}
        </div>

        {!poll.is_closed && (
          <VoteCard
            poll={poll}
            members={members ?? []}
            frontingIds={frontingIds}
            memberById={memberById}
          />
        )}

        <ResultsCard poll={poll} memberById={memberById} />

        <AuditCard
          events={audit?.events ?? []}
          isVisible={Boolean(audit?.is_visible)}
          memberById={memberById}
          options={poll.options}
        />
      </div>

      <DestructiveConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title="Delete poll?"
        description={`Permanently delete "${poll.question}". The audit log goes with it.`}
        tier={system?.delete_confirmation ?? "none"}
        onConfirm={(confirm) => deleteMut.mutate({ confirm })}
        loading={deleteMut.isPending}
      />
    </>
  );
}

function VoteCard({
  poll,
  members,
  frontingIds,
  memberById,
}: {
  poll: Poll;
  members: Member[];
  frontingIds: Set<string>;
  memberById: Map<string, Member>;
}) {
  const qc = useQueryClient();
  // Eligible voter set:
  //  - When the poll restricts voting to current fronters, only members
  //    in the front (minus custom fronts unless include_custom_fronts).
  //  - Otherwise, any system member, with the same custom-front exclusion.
  const eligibleMembers = useMemo(
    () =>
      members.filter((m) => {
        if (!poll.include_custom_fronts && m.is_custom_front) return false;
        if (poll.restrict_voting_to_fronters && !frontingIds.has(m.id)) {
          return false;
        }
        return true;
      }),
    [
      members,
      frontingIds,
      poll.include_custom_fronts,
      poll.restrict_voting_to_fronters,
    ],
  );

  const [selectedMemberId, setSelectedMemberId] = useState<string>(
    () => eligibleMembers[0]?.id ?? "",
  );
  const [picked, setPicked] = useState<Set<string>>(() => {
    if (!poll.votes || !selectedMemberId) return new Set();
    const mine = poll.votes.find(
      (v) => v.voted_as_member_id === selectedMemberId,
    );
    return new Set(mine?.option_ids ?? []);
  });

  const myExisting =
    poll.votes?.find((v) => v.voted_as_member_id === selectedMemberId) ?? null;

  const castMut = useMutation({
    mutationFn: () =>
      castVote(poll.id, {
        voted_as_member_id: selectedMemberId,
        option_ids: Array.from(picked),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["poll", poll.id] });
      qc.invalidateQueries({ queryKey: ["poll", poll.id, "audit"] });
      toast.success("Vote recorded");
    },
    onError: (err) => showApiErrorToast(err),
  });

  const withdrawMut = useMutation({
    mutationFn: () => withdrawVote(poll.id, selectedMemberId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["poll", poll.id] });
      qc.invalidateQueries({ queryKey: ["poll", poll.id, "audit"] });
      setPicked(new Set());
      toast.success("Vote withdrawn");
    },
    onError: (err) => showApiErrorToast(err),
  });

  if (eligibleMembers.length === 0) {
    const allFrontingAreCustom =
      poll.restrict_voting_to_fronters &&
      !poll.include_custom_fronts &&
      members.some((m) => frontingIds.has(m.id) && m.is_custom_front);
    return (
      <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
        {allFrontingAreCustom
          ? "Only custom fronts are currently fronting, and this poll doesn't accept votes from custom fronts."
          : poll.restrict_voting_to_fronters
          ? "No member is currently fronting. A member has to be in the front to cast or change a vote on this poll."
          : "No eligible voters in this system."}
      </div>
    );
  }

  function toggle(optionId: string) {
    if (poll.kind === "single_choice") {
      setPicked(new Set([optionId]));
    } else {
      const next = new Set(picked);
      if (next.has(optionId)) next.delete(optionId);
      else next.add(optionId);
      setPicked(next);
    }
  }

  function onSelectMember(id: string) {
    setSelectedMemberId(id);
    const mine = poll.votes?.find((v) => v.voted_as_member_id === id);
    setPicked(new Set(mine?.option_ids ?? []));
  }

  return (
    <div className="rounded-md border p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-sm">Voting as</span>
        <Select value={selectedMemberId} onValueChange={onSelectMember}>
          <SelectTrigger className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {eligibleMembers.map((m) => {
              const member = memberById.get(m.id) ?? m;
              return (
                <SelectItem key={m.id} value={m.id}>
                  {member.display_name || member.name}
                </SelectItem>
              );
            })}
          </SelectContent>
        </Select>
      </div>

      <div className="grid gap-2">
        {poll.options.map((opt) => (
          <label
            key={opt.id}
            className="flex items-center gap-2 rounded-md border p-2 cursor-pointer hover:bg-accent"
          >
            <Checkbox
              checked={picked.has(opt.id)}
              onCheckedChange={() => toggle(opt.id)}
            />
            <span className="flex-1">{opt.text}</span>
          </label>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <Button
          onClick={() => castMut.mutate()}
          disabled={
            picked.size === 0 ||
            castMut.isPending ||
            (poll.kind === "single_choice" && picked.size !== 1)
          }
        >
          {myExisting ? "Update vote" : "Cast vote"}
        </Button>
        {myExisting ? (
          <Button
            variant="ghost"
            onClick={() => withdrawMut.mutate()}
            disabled={withdrawMut.isPending}
          >
            Withdraw
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function ResultsCard({
  poll,
  memberById,
}: {
  poll: Poll;
  memberById: Map<string, Member>;
}) {
  const tally = poll.tally;
  if (tally === null || tally === undefined) {
    return (
      <div className="rounded-md border p-4 text-sm text-muted-foreground">
        Results are hidden until the poll closes.
      </div>
    );
  }

  const max = Math.max(1, ...tally.map((t) => t.count));
  return (
    <div className="rounded-md border p-4 space-y-3">
      <h3 className="font-medium text-sm">Results</h3>
      <div className="grid gap-2">
        {poll.options.map((opt) => {
          const entry = tally.find((t) => t.option_id === opt.id);
          const count = entry?.count ?? 0;
          const pct = (count / max) * 100;
          return (
            <div key={opt.id}>
              <div className="flex items-center justify-between text-sm">
                <span>{opt.text}</span>
                <span className="text-muted-foreground">{count}</span>
              </div>
              <div className="mt-1 h-2 rounded bg-muted">
                <div
                  className="h-full rounded bg-primary"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
      {poll.votes && poll.votes.length > 0 ? (
        <div className="mt-3 text-xs text-muted-foreground">
          <span className="font-medium">Voters: </span>
          {poll.votes
            .map((v) => {
              const m = memberById.get(v.voted_as_member_id);
              return m?.display_name || m?.name || "(deleted)";
            })
            .join(", ")}
        </div>
      ) : null}
    </div>
  );
}

function AuditCard({
  events,
  isVisible,
  memberById,
  options,
}: {
  events: PollVoteEvent[];
  isVisible: boolean;
  memberById: Map<string, Member>;
  options: { id: string; text: string }[];
}) {
  if (!isVisible) {
    return (
      <div className="rounded-md border p-4 text-sm text-muted-foreground">
        The audit log is hidden until the poll closes.
      </div>
    );
  }

  const optionById = new Map(options.map((o) => [o.id, o.text]));
  return (
    <div className="rounded-md border p-4 space-y-2">
      <h3 className="font-medium text-sm">Audit log</h3>
      {events.length === 0 ? (
        <p className="text-sm text-muted-foreground">No votes yet.</p>
      ) : (
        <div className="text-sm">
          <div className="grid grid-cols-[auto_auto_1fr] gap-x-3 gap-y-1">
            <div className="font-medium text-xs text-muted-foreground">
              When
            </div>
            <div className="font-medium text-xs text-muted-foreground">
              Action
            </div>
            <div className="font-medium text-xs text-muted-foreground">
              Detail
            </div>
            {events.map((e) => {
              const member = e.voted_as_member_id
                ? memberById.get(e.voted_as_member_id)
                : null;
              const memberLabel = member
                ? member.display_name || member.name
                : "(deleted)";
              const fronting = e.fronting_member_ids
                .map((id) => {
                  const m = memberById.get(id);
                  return m?.display_name || m?.name || id.slice(0, 6);
                })
                .join(", ");
              const opts = e.option_ids
                .map((id) => optionById.get(id) ?? "(removed)")
                .join(", ");
              return (
                <ContentRow
                  key={e.id}
                  when={new Date(e.created_at).toLocaleString()}
                  action={e.action}
                  detail={
                    <span>
                      <span className="font-medium">{memberLabel}</span>
                      {opts ? (
                        <>
                          {" "}
                          {e.action === "withdraw" ? "withdrew from" : "→"}{" "}
                          {opts}
                        </>
                      ) : null}
                      {fronting ? (
                        <span className="text-muted-foreground">
                          {" "}
                          (front: {fronting})
                        </span>
                      ) : null}
                    </span>
                  }
                />
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function ContentRow({
  when,
  action,
  detail,
}: {
  when: string;
  action: string;
  detail: React.ReactNode;
}) {
  return (
    <>
      <div className="text-xs text-muted-foreground">{when}</div>
      <div>
        <Badge variant="outline" className="capitalize">
          {action}
        </Badge>
      </div>
      <div>{detail}</div>
    </>
  );
}
