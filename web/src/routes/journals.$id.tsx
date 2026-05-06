import { Suspense, lazy, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";
import { ArrowLeft, History, Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  JournalEntryEditor,
  type JournalEntryEditorValue,
} from "@/components/journal-entry-editor";
import { ContentRevisionList } from "@/components/content-revision-list";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useMembers } from "@/hooks/use-members";
import { formatDateTime } from "@/lib/date-format";
import {
  deleteJournal,
  getJournal,
  listRevisions,
  pinJournalRevision,
  restoreRevision,
  unpinJournalRevision,
  updateJournal,
} from "@/lib/journals";
import { getSystemSafety } from "@/lib/system-safety";
import { getMySystem } from "@/lib/systems";
import { isDeleteQueued } from "@/types/api";

const MarkdownPreview = lazy(() =>
  import("@/components/bio-editor").then((m) => ({
    default: m.MarkdownPreview,
  })),
);

export function JournalDetailPage() {
  const { entryId = "" } = useParams<{ entryId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: members } = useMembers();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const { data: safety } = useQuery({
    queryKey: ["system-safety"],
    queryFn: getSystemSafety,
  });
  const dateFormat = system?.date_format ?? "ymd";

  const { data: entry, isLoading } = useQuery({
    queryKey: ["journal", entryId],
    queryFn: () => getJournal(entryId),
    enabled: !!entryId,
  });

  const [editing, setEditing] = useState(false);
  const [showRevisions, setShowRevisions] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const update = useMutation({
    mutationFn: (value: JournalEntryEditorValue) =>
      updateJournal(entryId, {
        title: value.title || null,
        body: value.body,
        author_member_ids: value.authorMemberIds,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["journal", entryId] });
      qc.invalidateQueries({ queryKey: ["journal", entryId, "revisions"] });
      qc.invalidateQueries({ queryKey: ["journals"] });
      setEditing(false);
      toast.success("Saved");
    },
  });

  if (!entryId) return null;
  if (isLoading) return <Skeleton className="h-64" />;
  if (!entry) {
    return (
      <p className="text-muted-foreground">Entry not found.</p>
    );
  }

  const member = entry.member_id ? members?.find((m) => m.id === entry.member_id) : null;
  const titleDisplay =
    entry.title || `Entry from ${formatDateTime(entry.created_at, dateFormat)}`;
  const authors = entry.author_member_names.length > 0
    ? entry.author_member_names.join(", ")
    : "account";

  return (
    <>
      <div className="mb-3">
        <Link
          to="/journals"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to journals
        </Link>
      </div>
      <PageHeader title={titleDisplay}>
        {!editing && (
          <>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setShowRevisions((s) => !s)}
            >
              <History className="h-4 w-4 mr-1" />
              Revisions {entry.revision_count > 0 && `(${entry.revision_count})`}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
              <Pencil className="h-4 w-4 mr-1" />
              Edit
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 className="h-4 w-4 mr-1" />
              Delete
            </Button>
          </>
        )}
      </PageHeader>

      <p className="mb-4 text-sm text-muted-foreground">
        {member ? (
          <>
            About <span className="font-medium">{member.display_name || member.name}</span>{" "}
            · written by {authors} · {formatDateTime(entry.created_at, dateFormat)}
          </>
        ) : (
          <>
            System-wide · written by {authors} ·{" "}
            {formatDateTime(entry.created_at, dateFormat)}
          </>
        )}
      </p>

      {showRevisions && !editing && (
        <Card className="mb-4">
          <CardContent className="p-4">
            <ContentRevisionList
              targetId={entry.id}
              currentBody={entry.body}
              queryKey={["journal", entry.id, "revisions"]}
              list={listRevisions}
              restore={restoreRevision}
              pin={pinJournalRevision}
              unpin={unpinJournalRevision}
              safetyEnabled={
                !!safety?.settings.applies_to_revisions &&
                (safety?.settings.grace_period_days ?? 0) > 0
              }
              authTier={safety?.settings.auth_tier ?? "none"}
              invalidateOnRestore={[
                ["journal", entry.id],
                ["journal", entry.id, "revisions"],
                ["system-safety"],
              ]}
              emptyMessage="No revisions yet. Edits to this entry will appear here."
              dateFormat={dateFormat}
            />
          </CardContent>
        </Card>
      )}

      {editing ? (
        <Card>
          <CardContent className="p-4">
            <JournalEntryEditor
              initial={{
                title: entry.title ?? "",
                body: entry.body,
                authorMemberIds: entry.author_member_ids,
              }}
              members={members ?? []}
              saving={update.isPending}
              onSubmit={(v) => update.mutate(v)}
              onCancel={() => setEditing(false)}
            />
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-4">
            <Suspense
              fallback={
                <p className="text-sm text-muted-foreground">Loading…</p>
              }
            >
              <MarkdownPreview content={entry.body} />
            </Suspense>
          </CardContent>
        </Card>
      )}

      {confirmDelete && (
        <DeleteEntryDialog
          entryId={entry.id}
          authTier={safety?.settings.auth_tier ?? "none"}
          onClose={() => setConfirmDelete(false)}
          onDeleted={(queued) => {
            qc.invalidateQueries({ queryKey: ["journals"] });
            if (queued) {
              qc.invalidateQueries({ queryKey: ["system-safety"] });
              toast.success(
                "Entry scheduled for deletion — cancellable from settings.",
              );
            } else {
              toast.success("Entry deleted");
            }
            navigate("/journals");
          }}
        />
      )}
    </>
  );
}

function DeleteEntryDialog({
  entryId,
  authTier,
  onClose,
  onDeleted,
}: {
  entryId: string;
  authTier: "none" | "password" | "totp" | "both";
  onClose: () => void;
  onDeleted: (queued: boolean) => void;
}) {
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState("");

  const del = useMutation({
    mutationFn: () =>
      deleteJournal(entryId, {
        password: password || undefined,
        totp_code: totpCode || undefined,
      }),
    onSuccess: (result) => {
      onDeleted(isDeleteQueued(result));
      onClose();
    },
    onError: (err) => setError(err instanceof Error ? err.message : "Failed"),
  });

  const needsPassword = authTier === "password" || authTier === "both";
  const needsTotp = authTier === "totp" || authTier === "both";

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete journal entry?</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            If a grace period is configured, this will be queued and cancellable.
            Otherwise it deletes immediately.
          </p>
          {needsPassword && (
            <div className="space-y-1">
              <Label className="text-sm">Password</Label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
          )}
          {needsTotp && (
            <div className="space-y-1">
              <Label className="text-sm">TOTP code</Label>
              <Input
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                placeholder="6-digit code"
                inputMode="numeric"
                maxLength={6}
                pattern="[0-9]{6}"
                autoComplete="off"
                required
              />
            </div>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={del.isPending}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => del.mutate()}
            disabled={del.isPending}
          >
            {del.isPending ? "Deleting…" : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
