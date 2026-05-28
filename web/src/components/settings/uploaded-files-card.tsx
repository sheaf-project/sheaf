import { useState } from "react";
import { Link } from "react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listFiles,
  deleteFile,
  getFileReferences,
  type FileReference,
  type UploadedFileInfo,
} from "@/lib/files";
import { getMySystem } from "@/lib/systems";
import { isDeleteQueued, type DestructiveConfirm } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { PendingDeleteBadge } from "@/components/pending-delete-badge";
import { cn, formatBytes } from "@/lib/utils";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";

/** In-app link for a reference target, or null if it isn't deep-linkable.
 * target_type covers both live refs ("system" / "member" / "journal_entry")
 * and revision refs ("member_bio" / "journal_entry"). */
function refHref(r: FileReference): string | null {
  switch (r.target_type) {
    case "system":
      return "/settings/system";
    case "member":
    case "member_bio":
      return `/members?member=${r.target_id}`;
    case "journal_entry":
      return `/journals/${r.target_id}`;
    default:
      return null;
  }
}

function ReferenceItem({ reference }: { reference: FileReference }) {
  const href = refHref(reference);
  return (
    <li className="flex items-baseline gap-2">
      <span className="text-muted-foreground">•</span>
      {href ? (
        <Link to={href} className="text-primary hover:underline">
          {reference.label}
        </Link>
      ) : (
        <span>{reference.label}</span>
      )}
    </li>
  );
}

export function UploadedFilesCard() {
  const qc = useQueryClient();
  const { data: files, isLoading } = useQuery({
    queryKey: ["files", "list"],
    queryFn: listFiles,
  });
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const [selected, setSelected] = useState<UploadedFileInfo | null>(null);
  const [deletingFile, setDeletingFile] = useState<UploadedFileInfo | null>(null);
  const remove = useMutation({
    mutationFn: ({ id, confirm }: { id: string; confirm?: DestructiveConfirm }) =>
      deleteFile(id, confirm),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["files", "list"] });
      qc.invalidateQueries({ queryKey: ["storage", "usage"] });
      setDeletingFile(null);
      if (isDeleteQueued(result)) {
        qc.invalidateQueries({ queryKey: ["system-safety"] });
        toast.success(
          `Image scheduled for deletion — cancellable in Settings until ${new Date(result.finalize_after).toLocaleDateString()}.`,
        );
      } else {
        toast.success("File deleted");
      }
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Delete failed"),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Uploaded files</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading...</p>
        )}
        {files && files.length === 0 && (
          <p className="text-sm text-muted-foreground">No uploaded files.</p>
        )}
        {files && files.length > 0 && (
          <>
            <p className="text-xs text-muted-foreground">
              Click an image to see where it&apos;s used.
            </p>
            <div className="grid grid-cols-4 sm:grid-cols-6 gap-2">
              {files.map((f: UploadedFileInfo) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => setSelected(f)}
                  className={cn(
                    "group relative aspect-square rounded-md border overflow-hidden text-left focus:outline-none focus:ring-2 focus:ring-ring",
                    f.pending_delete_at && "opacity-60",
                  )}
                  title={
                    f.pending_delete_at
                      ? `Pending delete - finalises ${new Date(f.pending_delete_at).toLocaleString()}. Open to manage.`
                      : undefined
                  }
                >
                  <img
                    src={f.url}
                    alt=""
                    className="h-full w-full object-cover"
                  />
                  <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors" />
                  {f.pending_delete_at && (
                    <span
                      className="absolute top-1 right-1 rounded-full bg-amber-500/90 p-0.5 text-white shadow"
                      aria-label="Pending delete"
                    >
                      <AlertTriangle className="h-3 w-3" />
                    </span>
                  )}
                  <span className="absolute bottom-0 left-0 right-0 bg-black/60 text-white text-[10px] px-1 py-0.5 truncate">
                    {f.purpose} · {formatBytes(f.size_bytes)}
                  </span>
                </button>
              ))}
            </div>
          </>
        )}
      </CardContent>

      <FileDetailDialog
        file={selected}
        onOpenChange={(open) => !open && setSelected(null)}
        onRequestDelete={(f) => {
          setSelected(null);
          setDeletingFile(f);
        }}
      />

      <DestructiveConfirmDialog
        open={!!deletingFile}
        onOpenChange={(open) => !open && setDeletingFile(null)}
        title="Delete image"
        description="Are you sure you want to delete this image? Anything still using it will show a broken image."
        tier={system?.delete_confirmation ?? "none"}
        onConfirm={(confirm) =>
          deletingFile && remove.mutate({ id: deletingFile.id, confirm })
        }
        loading={remove.isPending}
      />
    </Card>
  );
}

function FileDetailDialog({
  file,
  onOpenChange,
  onRequestDelete,
}: {
  file: UploadedFileInfo | null;
  onOpenChange: (open: boolean) => void;
  onRequestDelete: (file: UploadedFileInfo) => void;
}) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["files", file?.id, "references"],
    queryFn: () => getFileReferences(file!.id),
    enabled: !!file,
  });

  const refs = data?.references ?? [];
  const liveRefs = refs.filter((r) => r.kind !== "revision");
  const historyRefs = refs.filter((r) => r.kind === "revision");
  const historyOnly = liveRefs.length === 0 && historyRefs.length > 0;

  return (
    <Dialog open={!!file} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Image details</DialogTitle>
          {file && (
            <DialogDescription>
              {file.purpose} · {formatBytes(file.size_bytes)}
            </DialogDescription>
          )}
        </DialogHeader>

        {file && (
          <div className="space-y-4">
            <img
              src={file.url}
              alt=""
              className="max-h-48 w-auto rounded-md border mx-auto"
            />

            <PendingDeleteBadge finalizeAt={file.pending_delete_at} />

            <div className="space-y-3">
              {isLoading && (
                <p className="text-sm text-muted-foreground">
                  Checking references...
                </p>
              )}
              {isError && (
                <p className="text-sm text-destructive">
                  Couldn&apos;t load references.
                </p>
              )}
              {!isLoading && !isError && refs.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  Not used anywhere. Deleting it won&apos;t break anything.
                </p>
              )}

              {!isLoading && !isError && historyOnly && (
                <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">
                  Only kept by edit history — it isn&apos;t shown anywhere
                  current. Deleting it is safe unless you restore an older
                  revision below. (Orphan cleanup leaves these in place.)
                </p>
              )}

              {!isLoading && !isError && liveRefs.length > 0 && (
                <div className="space-y-1">
                  <p className="text-sm font-medium">Used in</p>
                  <ul className="space-y-1 text-sm">
                    {liveRefs.map((r, i) => (
                      <ReferenceItem
                        key={`${r.kind}-${r.target_id}-${i}`}
                        reference={r}
                      />
                    ))}
                  </ul>
                </div>
              )}

              {!isLoading && !isError && historyRefs.length > 0 && (
                <div className="space-y-1">
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    Edit history
                  </p>
                  <ul className="space-y-1 text-sm">
                    {historyRefs.map((r, i) => (
                      <ReferenceItem
                        key={`${r.kind}-${r.target_id}-${i}`}
                        reference={r}
                      />
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          {file && (
            <Button
              variant="destructive"
              onClick={() => onRequestDelete(file)}
              disabled={!!file.pending_delete_at}
              title={
                file.pending_delete_at
                  ? "Already queued for deletion. Cancel from Settings -> Safety."
                  : undefined
              }
            >
              Delete
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
