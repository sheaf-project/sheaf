import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listFiles,
  deleteFile,
  getFileReferences,
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
import { formatBytes } from "@/lib/utils";
import { toast } from "sonner";

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
                  className="group relative aspect-square rounded-md border overflow-hidden text-left focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <img
                    src={f.url}
                    alt=""
                    className="h-full w-full object-cover"
                  />
                  <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors" />
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

            <div className="space-y-2">
              <p className="text-sm font-medium">Used in</p>
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
              {!isLoading && !isError && refs.length > 0 && (
                <ul className="space-y-1 text-sm">
                  {refs.map((r, i) => (
                    <li
                      key={`${r.kind}-${r.target_id}-${i}`}
                      className="flex items-baseline gap-2"
                    >
                      <span className="text-muted-foreground">•</span>
                      <span>{r.label}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          {file && (
            <Button variant="destructive" onClick={() => onRequestDelete(file)}>
              Delete
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
