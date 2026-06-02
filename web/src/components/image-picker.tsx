import { useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listFiles, uploadFile } from "@/lib/files";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Upload, ImagePlus, Link } from "lucide-react";
import { AvatarCropperDialog } from "@/components/avatar-cropper-dialog";
import { useAuth } from "@/hooks/use-auth";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

export function ImagePickerDialog({
  open,
  onOpenChange,
  onSelect,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (markdown: string) => void;
}) {
  const { user } = useAuth();
  const uploadsAllowed = user?.bio_uploads_allowed ?? true;
  const externalAllowed = user?.external_images_allowed ?? true;
  const [tab, setTab] = useState<string>(uploadsAllowed ? "upload" : "existing");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add image</DialogTitle>
        </DialogHeader>
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="w-full">
            {uploadsAllowed && (
              <TabsTrigger value="upload" className="flex-1 gap-1.5">
                <Upload className="h-3.5 w-3.5" />
                Upload
              </TabsTrigger>
            )}
            <TabsTrigger value="existing" className="flex-1 gap-1.5">
              <ImagePlus className="h-3.5 w-3.5" />
              Existing
            </TabsTrigger>
            {externalAllowed && (
              <TabsTrigger value="external" className="flex-1 gap-1.5">
                <Link className="h-3.5 w-3.5" />
                External URL
              </TabsTrigger>
            )}
          </TabsList>
          {uploadsAllowed && (
            <TabsContent value="upload" className="mt-3">
              <UploadTab
                onUploaded={(key) => {
                  onSelect(`![image](/v1/files/${key})`);
                  onOpenChange(false);
                }}
              />
            </TabsContent>
          )}
          <TabsContent value="existing" className="mt-3">
            <ExistingTab
              onSelect={(key) => {
                onSelect(`![image](/v1/files/${key})`);
                onOpenChange(false);
              }}
            />
          </TabsContent>
          {externalAllowed && (
            <TabsContent value="external" className="mt-3">
              <ExternalTab
                onSelect={(url) => {
                  onSelect(`![image](${url})`);
                  onOpenChange(false);
                }}
              />
            </TabsContent>
          )}
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

function UploadTab({ onUploaded }: { onUploaded: (key: string) => void }) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState("");
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const qc = useQueryClient();
  const upload = useMutation({
    mutationFn: (file: File) => uploadFile(file, "bio"),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["files", "list"] });
      qc.invalidateQueries({ queryKey: ["storage", "usage"] });
      onUploaded(res.key);
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "Upload failed");
    },
  });

  function handleCroppedBlob(blob: Blob) {
    setPendingFile(null);
    setError("");
    // Bio uploads default to JPEG (better compression for photos); the
    // server normalizes anyway so the extension is just a hint here.
    const ext = blob.type === "image/jpeg" ? "jpg" : "png";
    upload.mutate(new File([blob], `bio.${ext}`, { type: blob.type }));
  }

  return (
    <div className="space-y-3">
      <div
        className="flex flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed border-muted-foreground/25 p-6 cursor-pointer hover:border-muted-foreground/50 transition-colors"
        onClick={() => fileInputRef.current?.click()}
      >
        <Upload className="h-8 w-8 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">
          {upload.isPending ? "Uploading..." : "Click to select an image"}
        </p>
        <p className="text-xs text-muted-foreground">JPEG, PNG, GIF, or WebP</p>
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/jpeg,image/png,image/gif,image/webp"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) setPendingFile(file);
          e.target.value = "";
        }}
      />
      <AvatarCropperDialog
        open={pendingFile !== null}
        file={pendingFile}
        // Freeform crop for bio embeds: no aspect lock, rectangular preview.
        aspect={undefined}
        cropShape="rect"
        outputMime="image/jpeg"
        onConfirm={handleCroppedBlob}
        onCancel={() => setPendingFile(null)}
      />
    </div>
  );
}

function ExistingTab({ onSelect }: { onSelect: (key: string) => void }) {
  const { data: files, isLoading } = useQuery({
    queryKey: ["files", "list"],
    queryFn: listFiles,
  });

  if (isLoading) {
    return <p className="text-sm text-muted-foreground py-4 text-center">Loading...</p>;
  }

  if (!files || files.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4 text-center">
        No uploaded images yet.
      </p>
    );
  }

  return (
    <div className="grid grid-cols-3 gap-2 max-h-64 overflow-y-auto">
      {files.map((f) => (
        <button
          key={f.id}
          type="button"
          className="group relative aspect-square rounded-md border overflow-hidden hover:ring-2 hover:ring-ring transition-all"
          onClick={() => onSelect(f.key)}
          title={`${f.key.split("/").pop()} (${formatBytes(f.size_bytes)})`}
        >
          <img
            src={f.url}
            alt=""
            className="h-full w-full object-cover"
          />
          <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors" />
          <span className="absolute bottom-0 left-0 right-0 bg-black/60 text-white text-[10px] px-1 py-0.5 truncate opacity-0 group-hover:opacity-100 transition-opacity">
            {f.purpose} · {formatBytes(f.size_bytes)}
          </span>
        </button>
      ))}
    </div>
  );
}

function ExternalTab({ onSelect }: { onSelect: (url: string) => void }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // This dialog is rendered via a portal but React still bubbles submit
    // events through the virtual tree, so without stopPropagation the parent
    // profile <form> saves with stale bio state before our onSelect inserts
    // the image markdown.
    e.stopPropagation();
    const trimmed = url.trim();
    if (!trimmed) return;
    if (!trimmed.startsWith("https://")) {
      setError("URL must start with https://");
      return;
    }
    onSelect(trimmed);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="external-url" className="text-sm">Image URL</Label>
        <Input
          id="external-url"
          type="url"
          placeholder="https://example.com/image.png"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setError("");
          }}
        />
        {error && <p className="text-xs text-destructive">{error}</p>}
        <p className="text-xs text-muted-foreground">
          Must be HTTPS. External images may be blocked by server policy.
        </p>
      </div>
      <Button type="submit" size="sm" disabled={!url.trim()}>
        Insert
      </Button>
    </form>
  );
}
