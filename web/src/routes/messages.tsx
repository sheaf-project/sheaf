import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import {
  ChevronDown,
  ChevronRight,
  CornerDownRight,
  History,
  MessageSquare,
  Pencil,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { useMembers } from "@/hooks/use-members";
import { getCurrentFronts } from "@/lib/fronts";
import { getMySystem } from "@/lib/systems";
import {
  deleteMessage,
  deleteThread,
  editMessage,
  listBoardMessages,
  listBoards,
  listMessageRevisions,
  markBoardSeen,
  pinMessageRevision,
  postMessage,
  restoreMessageRevision,
  unpinMessageRevision,
} from "@/lib/messages";
import { ContentRevisionList } from "@/components/content-revision-list";
import { getSystemSafety } from "@/lib/system-safety";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { isDeleteQueued } from "@/types/api";
import type {
  BoardKind,
  BoardSummary,
  DestructiveConfirm,
  Member,
  Message,
} from "@/types/api";

type TabKind = "global" | "members";

export function MessagesPage() {
  const { data: members } = useMembers();
  const { data: currentFronts } = useQuery({
    queryKey: ["fronts", "current"],
    queryFn: getCurrentFronts,
  });

  const callerMemberId = useMemo(() => {
    const fronting = (currentFronts ?? []).flatMap((f) => f.member_ids);
    return fronting[0] ?? null;
  }, [currentFronts]);

  const { data: boards } = useQuery({
    queryKey: ["messages", "boards", callerMemberId],
    queryFn: () => listBoards(callerMemberId ?? undefined),
  });

  const [searchParams] = useSearchParams();
  const deepLinkedMemberId = searchParams.get("member");
  const [tab, setTab] = useState<TabKind>(
    deepLinkedMemberId ? "members" : "global",
  );
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(
    deepLinkedMemberId,
  );
  const [memberFilter, setMemberFilter] = useState("");

  const memberBoards = useMemo(
    () => (boards ?? []).filter((b) => b.board_kind === "member"),
    [boards],
  );
  const filteredMemberBoards = useMemo(() => {
    if (!memberFilter.trim()) return memberBoards;
    const q = memberFilter.toLowerCase();
    return memberBoards.filter((b) =>
      (b.member_name ?? "").toLowerCase().includes(q),
    );
  }, [memberBoards, memberFilter]);

  return (
    <>
      <PageHeader title="Messages" />
      <div className="mb-3 flex gap-2 border-b">
        <button
          onClick={() => setTab("global")}
          className={`px-3 py-2 text-sm border-b-2 -mb-px ${
            tab === "global"
              ? "border-primary font-medium"
              : "border-transparent text-muted-foreground"
          }`}
        >
          Global
        </button>
        <button
          onClick={() => setTab("members")}
          className={`px-3 py-2 text-sm border-b-2 -mb-px ${
            tab === "members"
              ? "border-primary font-medium"
              : "border-transparent text-muted-foreground"
          }`}
        >
          Members
        </button>
      </div>

      {tab === "global" && (
        <BoardView
          boardKind="system"
          boardMemberId={null}
          callerMemberId={callerMemberId}
          members={members ?? []}
        />
      )}

      {tab === "members" && (
        <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
          <div className="space-y-2">
            <input
              type="text"
              value={memberFilter}
              onChange={(e) => setMemberFilter(e.target.value)}
              placeholder="Search members..."
              className="w-full rounded-md border bg-background p-2 text-sm"
            />
            <div className="space-y-1 max-h-[60vh] overflow-y-auto">
              {filteredMemberBoards.map((b) => (
                <button
                  key={b.board_member_id}
                  onClick={() => setSelectedMemberId(b.board_member_id)}
                  className={`w-full text-left rounded-md p-2 text-sm hover:bg-accent ${
                    selectedMemberId === b.board_member_id ? "bg-accent" : ""
                  }`}
                >
                  <BoardListEntry summary={b} />
                </button>
              ))}
              {filteredMemberBoards.length === 0 && (
                <p className="p-2 text-xs text-muted-foreground">
                  No members match.
                </p>
              )}
            </div>
          </div>
          <div>
            {selectedMemberId ? (
              <BoardView
                key={selectedMemberId}
                boardKind="member"
                boardMemberId={selectedMemberId}
                callerMemberId={callerMemberId}
                members={members ?? []}
              />
            ) : (
              <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
                Select a member to view their wall.
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function BoardListEntry({ summary }: { summary: BoardSummary }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">
            {summary.member_name ?? "Global"}
          </span>
          {summary.unread_count > 0 && (
            <Badge variant="default">{summary.unread_count}</Badge>
          )}
        </div>
        {summary.last_message_preview && (
          <p className="truncate text-xs text-muted-foreground">
            {summary.last_message_preview}
          </p>
        )}
      </div>
    </div>
  );
}

function BoardView({
  boardKind,
  boardMemberId,
  callerMemberId,
  members,
}: {
  boardKind: BoardKind;
  boardMemberId: string | null;
  callerMemberId: string | null;
  members: Member[];
}) {
  const qc = useQueryClient();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });

  const queryKey = [
    "messages",
    "board",
    boardKind,
    boardMemberId,
    callerMemberId,
  ];
  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () =>
      listBoardMessages(boardKind, boardMemberId, callerMemberId ?? undefined),
  });

  // Mark this board as seen for the caller-member after the page renders
  // — the act of looking IS the act of reading. The AbortController cancels
  // the in-flight request on unmount, which also collapses StrictMode's
  // double-invoke into a single effective POST.
  useEffect(() => {
    if (!callerMemberId) return;
    const controller = new AbortController();
    markBoardSeen(callerMemberId, boardKind, boardMemberId, controller.signal)
      .then(() => {
        if (controller.signal.aborted) return;
        qc.invalidateQueries({ queryKey: ["messages", "boards"] });
        qc.invalidateQueries({ queryKey: ["messages", "unread"] });
      })
      .catch((err) => {
        // Best-effort, but a real failure shouldn't vanish silently.
        if (controller.signal.aborted) return;
        console.error("Failed to mark board as seen", err);
      });
    return () => controller.abort();
  }, [boardKind, boardMemberId, callerMemberId, qc]);

  const memberById = useMemo(
    () => new Map<string, Member>(members.map((m) => [m.id, m])),
    [members],
  );

  const [replyTo, setReplyTo] = useState<Message | null>(null);
  const [editing, setEditing] = useState<Message | null>(null);
  const [historyFor, setHistoryFor] = useState<Message | null>(null);
  const [deleting, setDeleting] = useState<{
    msg: Message;
    cascade: boolean;
  } | null>(null);
  const [viewMode, setViewMode] = useState<"flat" | "topics">("flat");
  const [expandedThreads, setExpandedThreads] = useState<Set<string>>(
    () => new Set(),
  );

  const deleteMut = useMutation({
    mutationFn: ({
      msg,
      cascade,
      confirm,
    }: {
      msg: Message;
      cascade: boolean;
      confirm?: DestructiveConfirm;
    }) =>
      cascade ? deleteThread(msg.id, confirm) : deleteMessage(msg.id, confirm),
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ["messages"] });
      setDeleting(null);
      if (isDeleteQueued(resp)) {
        toast.success(
          `Deletion queued. Will finalize after ${new Date(
            resp.finalize_after,
          ).toLocaleString()} unless cancelled.`,
        );
      } else {
        toast.success("Deleted");
      }
    },
  });

  // Topics mode groups messages by thread root (recursing parent_message_id
  // until we hit a top-level post). Replies to messages on a different page
  // — i.e. parent not in `data.messages` — are treated as their own root,
  // matching what the user sees in flat mode anyway.
  const allMessages = useMemo(() => data?.messages ?? [], [data?.messages]);
  const messageById = useMemo(
    () => new Map(allMessages.map((m) => [m.id, m])),
    [allMessages],
  );
  const threadGroups = useMemo(() => {
    const findRoot = (m: Message): Message => {
      let cur = m;
      while (cur.parent_message_id) {
        const parent = messageById.get(cur.parent_message_id);
        if (!parent) return cur;
        cur = parent;
      }
      return cur;
    };
    const byRoot = new Map<string, { root: Message; replies: Message[] }>();
    for (const m of allMessages) {
      const root = findRoot(m);
      if (!byRoot.has(root.id)) byRoot.set(root.id, { root, replies: [] });
      if (m.id !== root.id) byRoot.get(root.id)!.replies.push(m);
    }
    for (const g of byRoot.values()) {
      g.replies.sort(
        (a, b) =>
          new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      );
    }
    return Array.from(byRoot.values()).sort(
      (a, b) =>
        new Date(b.root.created_at).getTime() -
        new Date(a.root.created_at).getTime(),
    );
  }, [allMessages, messageById]);

  if (isLoading || !data) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
      </div>
    );
  }

  const lastSeen = data.caller_last_seen_at
    ? new Date(data.caller_last_seen_at).getTime()
    : null;

  return (
    <div className="space-y-3">
      <Composer
        boardKind={boardKind}
        boardMemberId={boardMemberId}
        members={members}
        replyTo={replyTo}
        onClearReply={() => setReplyTo(null)}
        onPosted={() => qc.invalidateQueries({ queryKey })}
      />

      <div className="flex items-center justify-end gap-1 text-xs">
        <span className="text-muted-foreground">View:</span>
        <Button
          size="sm"
          variant={viewMode === "flat" ? "default" : "ghost"}
          className="h-7 px-2 text-xs"
          onClick={() => setViewMode("flat")}
        >
          Flat
        </Button>
        <Button
          size="sm"
          variant={viewMode === "topics" ? "default" : "ghost"}
          className="h-7 px-2 text-xs"
          onClick={() => setViewMode("topics")}
        >
          Topics
        </Button>
      </div>

      <div className="space-y-2">
        {data.messages.length === 0 ? (
          <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
            <MessageSquare className="mx-auto mb-1 size-5 opacity-50" />
            No messages yet.
          </div>
        ) : viewMode === "flat" ? (
          data.messages.map((msg) => (
            <MessageRow
              key={msg.id}
              msg={msg}
              memberById={memberById}
              isUnread={
                lastSeen !== null && new Date(msg.created_at).getTime() > lastSeen
              }
              onReply={() => setReplyTo(msg)}
              onEdit={() => setEditing(msg)}
              onHistory={() => setHistoryFor(msg)}
              onDelete={(cascade) => setDeleting({ msg, cascade })}
            />
          ))
        ) : (
          threadGroups.map((group) => {
            const expanded = expandedThreads.has(group.root.id);
            const replyCount = group.replies.length;
            return (
              <div key={group.root.id} className="space-y-2">
                <div className="flex items-start gap-1">
                  <button
                    type="button"
                    onClick={() =>
                      setExpandedThreads((prev) => {
                        const next = new Set(prev);
                        if (next.has(group.root.id)) next.delete(group.root.id);
                        else next.add(group.root.id);
                        return next;
                      })
                    }
                    disabled={replyCount === 0}
                    aria-label={expanded ? "Collapse replies" : "Expand replies"}
                    className="mt-3 shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-30"
                  >
                    {expanded ? (
                      <ChevronDown className="size-4" />
                    ) : (
                      <ChevronRight className="size-4" />
                    )}
                  </button>
                  <div className="flex-1">
                    <MessageRow
                      msg={group.root}
                      memberById={memberById}
                      isUnread={
                        lastSeen !== null &&
                        new Date(group.root.created_at).getTime() > lastSeen
                      }
                      onReply={() => setReplyTo(group.root)}
                      onEdit={() => setEditing(group.root)}
                      onHistory={() => setHistoryFor(group.root)}
                      onDelete={(cascade) =>
                        setDeleting({ msg: group.root, cascade })
                      }
                      replyCount={replyCount}
                    />
                  </div>
                </div>
                {expanded && replyCount > 0 && (
                  <div className="ml-6 space-y-2 border-l pl-3">
                    {group.replies.map((reply) => (
                      <MessageRow
                        key={reply.id}
                        msg={reply}
                        memberById={memberById}
                        isUnread={
                          lastSeen !== null &&
                          new Date(reply.created_at).getTime() > lastSeen
                        }
                        onReply={() => setReplyTo(reply)}
                        onEdit={() => setEditing(reply)}
                        onHistory={() => setHistoryFor(reply)}
                        onDelete={(cascade) =>
                          setDeleting({ msg: reply, cascade })
                        }
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {editing && (
        <EditDialog
          message={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            qc.invalidateQueries({ queryKey });
          }}
        />
      )}

      {historyFor && (
        <HistoryDialog
          message={historyFor}
          dateFormat={system?.date_format ?? "ymd"}
          onClose={() => setHistoryFor(null)}
          onRestored={() => {
            setHistoryFor(null);
            qc.invalidateQueries({ queryKey });
          }}
        />
      )}

      <DestructiveConfirmDialog
        open={Boolean(deleting)}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title={deleting?.cascade ? "Delete thread?" : "Delete message?"}
        description={
          deleting
            ? deleting.cascade
              ? "Removes this message and every reply in its chain."
              : "Removes just this message. Replies stay in place with the parent shown as [deleted]."
            : ""
        }
        tier={system?.delete_confirmation ?? "none"}
        onConfirm={(confirm) => {
          if (deleting) deleteMut.mutate({ ...deleting, confirm });
        }}
        loading={deleteMut.isPending}
      />
    </div>
  );
}

function MessageRow({
  msg,
  memberById,
  isUnread,
  onReply,
  onEdit,
  onHistory,
  onDelete,
  replyCount,
}: {
  msg: Message;
  memberById: Map<string, Member>;
  isUnread: boolean;
  onReply: () => void;
  onEdit: () => void;
  onHistory: () => void;
  onDelete: (cascade: boolean) => void;
  replyCount?: number;
}) {
  const author =
    msg.author_member_id !== null
      ? (memberById.get(msg.author_member_id)?.display_name ??
        msg.author_member_name ??
        "[deleted member]")
      : "[deleted member]";

  return (
    <div
      className={`rounded-md border p-3 ${
        isUnread ? "border-primary/60 bg-primary/5" : ""
      }`}
    >
      {msg.parent_message_id && (
        <div className="mb-1 flex items-start gap-1 text-xs text-muted-foreground">
          <CornerDownRight className="mt-0.5 size-3 shrink-0" />
          {msg.parent_preview ? (
            <span className="truncate">
              Replying to{" "}
              <span className="font-medium">
                {msg.parent_author_member_name ?? "[deleted]"}
              </span>
              : {msg.parent_preview}
            </span>
          ) : (
            <span className="italic">Replying to a deleted message</span>
          )}
        </div>
      )}
      <div className="flex items-center justify-between gap-2 text-sm">
        <div className="flex items-center gap-2">
          <span className="font-medium">{author}</span>
          {replyCount !== undefined && replyCount > 0 && (
            <Badge variant="secondary" className="text-xs">
              {replyCount} {replyCount === 1 ? "reply" : "replies"}
            </Badge>
          )}
        </div>
        <span className="text-xs text-muted-foreground">
          {new Date(msg.created_at).toLocaleString()}
          {msg.updated_at && msg.updated_at !== msg.created_at && (
            <span className="ml-1 italic">(edited)</span>
          )}
        </span>
      </div>
      <p className="mt-1 whitespace-pre-wrap text-sm">{msg.body}</p>
      <div className="mt-2 flex gap-1">
        <Button size="sm" variant="ghost" onClick={onReply}>
          Reply
        </Button>
        <Button size="sm" variant="ghost" onClick={onEdit}>
          <Pencil className="size-3" />
          Edit
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={onHistory}
          disabled={!msg.updated_at || msg.updated_at === msg.created_at}
          title={
            msg.updated_at && msg.updated_at !== msg.created_at
              ? "Message history"
              : "No edit history"
          }
        >
          <History className="size-3" />
          History
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onDelete(false)}>
          <Trash2 className="size-3" />
          Delete
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onDelete(true)}>
          <Trash2 className="size-3" />
          Thread
        </Button>
      </div>
    </div>
  );
}

function Composer({
  boardKind,
  boardMemberId,
  members,
  replyTo,
  onClearReply,
  onPosted,
}: {
  boardKind: BoardKind;
  boardMemberId: string | null;
  members: Member[];
  replyTo: Message | null;
  onClearReply: () => void;
  onPosted: () => void;
}) {
  const [authorId, setAuthorId] = useState<string>(() => members[0]?.id ?? "");
  const [body, setBody] = useState("");

  const effectiveAuthorId = authorId || members[0]?.id || "";

  const postMut = useMutation({
    mutationFn: postMessage,
    onSuccess: () => {
      setBody("");
      onClearReply();
      onPosted();
    },
    onError: (err: Error) => toast.error(err.message),
  });

  function submit() {
    if (!body.trim() || !effectiveAuthorId) return;
    postMut.mutate({
      board_kind: boardKind,
      board_member_id: boardMemberId,
      author_member_id: effectiveAuthorId,
      parent_message_id: replyTo?.id ?? null,
      body: body.trim(),
    });
  }

  return (
    <div className="rounded-md border p-3 space-y-2">
      {replyTo && (
        <div className="flex items-start justify-between gap-2 rounded bg-muted px-2 py-1 text-xs">
          <div className="min-w-0 flex-1">
            Replying to{" "}
            <span className="font-medium">
              {replyTo.author_member_name ?? "[deleted]"}
            </span>
            : <span className="italic">{replyTo.body.slice(0, 80)}</span>
          </div>
          <button
            onClick={onClearReply}
            className="text-muted-foreground hover:text-foreground"
          >
            ×
          </button>
        </div>
      )}
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">Posting as</span>
        <Select value={effectiveAuthorId} onValueChange={setAuthorId}>
          <SelectTrigger className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {members.map((m) => (
              <SelectItem key={m.id} value={m.id}>
                {m.display_name || m.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={3}
        maxLength={5000}
        placeholder="Write a message..."
        className="w-full rounded-md border bg-background p-2 text-sm"
      />
      <div className="flex justify-end">
        <Button onClick={submit} disabled={!body.trim() || postMut.isPending}>
          {replyTo ? "Reply" : "Post"}
        </Button>
      </div>
    </div>
  );
}

function EditDialog({
  message,
  onClose,
  onSaved,
}: {
  message: Message;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [body, setBody] = useState(message.body);
  const editMut = useMutation({
    mutationFn: () => editMessage(message.id, { body }),
    onSuccess: () => {
      toast.success("Edited");
      onSaved();
    },
    onError: (err: Error) => toast.error(err.message),
  });
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80">
      <div className="w-[min(500px,90vw)] rounded-md border bg-background p-4 space-y-3">
        <h2 className="text-sm font-medium">Edit message</h2>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={5}
          maxLength={5000}
          className="w-full rounded-md border bg-background p-2 text-sm"
        />
        <p className="text-xs text-muted-foreground">
          Edits are tracked in revision history.
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => editMut.mutate()}
            disabled={!body.trim() || editMut.isPending}
          >
            Save
          </Button>
        </div>
      </div>
    </div>
  );
}

function HistoryDialog({
  message,
  dateFormat,
  onClose,
  onRestored,
}: {
  message: Message;
  dateFormat: import("@/types/api").DateFormat;
  onClose: () => void;
  onRestored: () => void;
}) {
  const { data: safety } = useQuery({
    queryKey: ["system-safety"],
    queryFn: getSystemSafety,
  });
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Message history</DialogTitle>
        </DialogHeader>
        <ContentRevisionList
          targetId={message.id}
          currentBody={message.body}
          queryKey={["messages", "revisions", message.id]}
          list={listMessageRevisions}
          restore={(id, revisionId) =>
            restoreMessageRevision(id, revisionId).then(() => onRestored())
          }
          pin={pinMessageRevision}
          unpin={unpinMessageRevision}
          safetyEnabled={
            !!safety?.settings.applies_to_revisions &&
            (safety?.settings.grace_period_days ?? 0) > 0
          }
          authTier={safety?.settings.auth_tier ?? "none"}
          invalidateOnRestore={[
            ["messages"],
            ["messages", "revisions", message.id],
          ]}
          emptyMessage="No revisions yet. Edits to this message will appear here."
          dateFormat={dateFormat}
        />
      </DialogContent>
    </Dialog>
  );
}

// Re-export so the sidebar can import a Link type without pulling in the
// full route file in tree-shaking scenarios.
export const messagesIndexHref = "/messages";
export { Link as RouterLink };
