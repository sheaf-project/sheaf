import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listFiles, deleteFile, type UploadedFileInfo } from "@/lib/files";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatBytes } from "@/lib/utils";
import { toast } from "sonner";

export function UploadedFilesCard() {
  const qc = useQueryClient();
  const { data: files, isLoading } = useQuery({
    queryKey: ["files", "list"],
    queryFn: listFiles,
  });
  const remove = useMutation({
    mutationFn: deleteFile,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["files", "list"] });
      qc.invalidateQueries({ queryKey: ["storage", "usage"] });
      toast.success("File deleted");
    },
  });
  const [confirmId, setConfirmId] = useState<string | null>(null);

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
          <div className="grid grid-cols-4 sm:grid-cols-6 gap-2">
            {files.map((f: UploadedFileInfo) => (
              <div
                key={f.id}
                className="group relative aspect-square rounded-md border overflow-hidden"
              >
                <img
                  src={f.url}
                  alt=""
                  className="h-full w-full object-cover"
                />
                <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-colors" />
                <div className="absolute top-0 right-0 opacity-0 group-hover:opacity-100 transition-opacity p-0.5">
                  {confirmId === f.id ? (
                    <Button
                      variant="destructive"
                      size="sm"
                      className="h-5 text-[10px] px-1"
                      onClick={() => {
                        remove.mutate(f.id);
                        setConfirmId(null);
                      }}
                      disabled={remove.isPending}
                    >
                      Confirm
                    </Button>
                  ) : (
                    <Button
                      variant="destructive"
                      size="sm"
                      className="h-5 text-[10px] px-1"
                      onClick={() => setConfirmId(f.id)}
                    >
                      Delete
                    </Button>
                  )}
                </div>
                <span className="absolute bottom-0 left-0 right-0 bg-black/60 text-white text-[10px] px-1 py-0.5 truncate">
                  {f.purpose} · {formatBytes(f.size_bytes)}
                </span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
