import { type FormEvent, useRef, useState } from "react";
import { toast } from "sonner";
import { uploadFile } from "@/lib/files";
import { apiErrorMessage } from "@/lib/api-errors";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { AvatarCropperDialog } from "@/components/avatar-cropper-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Camera, X, Link } from "lucide-react";
import { useAuth } from "@/hooks/use-auth";

export function AvatarUpload({
  url,
  fallback,
  onUpload,
  onRemove,
}: {
  url: string | null;
  fallback: string;
  onUpload: (key: string) => void;
  onRemove?: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [showUrlInput, setShowUrlInput] = useState(false);
  const [urlValue, setUrlValue] = useState("");
  // Signed preview URL after upload — separate from the stored key
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  // File staged for the cropper; non-null while the dialog is open.
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const { user } = useAuth();
  const uploadsAllowed = user?.uploads_allowed ?? true;
  const externalAllowed = user?.external_images_allowed ?? true;

  async function handleCroppedBlob(blob: Blob) {
    setPendingFile(null);
    setError("");
    setUploading(true);
    try {
      // Wrap the Blob back into a File so the FormData entry carries a
      // sensible filename and content-type; the backend re-derives the
      // extension from the magic-byte sniff anyway.
      const named = new File([blob], "avatar.png", { type: blob.type });
      const res = await uploadFile(named);
      setPreviewUrl(res.url); // show signed URL immediately
      onUpload(res.key);      // store the key, not the expiring URL
      // Canvas crop already strips animation, so res.animated here means
      // the original source was animated and the cropper flattened it.
      // Tell the user so they aren't surprised by a still avatar.
      if (res.animated) {
        toast.info("Animated image was flattened to a single frame.");
      }
    } catch (err) {
      setError(apiErrorMessage(err, "Upload failed"));
    } finally {
      setUploading(false);
    }
  }

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) setPendingFile(file);
    e.target.value = "";
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith("image/")) setPendingFile(file);
  }

  function handleUrlSubmit(e: FormEvent) {
    e.preventDefault();
    // This <form> is nested inside the parent profile <form>. Submit events
    // bubble through React's tree (even across Radix portals), so without
    // stopPropagation the parent form saves with stale state before our
    // onUpload takes effect.
    e.stopPropagation();
    const trimmed = urlValue.trim();
    if (!trimmed) return;
    // Allowlist the scheme so a javascript:/data:/file: URL can't ride in
    // as an avatar src. http and https are both fine for an image URL.
    if (!/^https?:\/\//i.test(trimmed)) {
      setError("Image URL must start with http:// or https://");
      return;
    }
    setError("");
    onUpload(trimmed);
    setUrlValue("");
    setShowUrlInput(false);
  }

  return (
    <div className="flex items-center gap-3">
      <div
        className={`group relative ${uploadsAllowed ? "cursor-pointer" : ""}`}
        onClick={uploadsAllowed ? () => inputRef.current?.click() : undefined}
        onDrop={uploadsAllowed ? handleDrop : undefined}
        onDragOver={uploadsAllowed ? (e) => e.preventDefault() : undefined}
      >
        <Avatar className="size-20">
          {(previewUrl ?? url) && <AvatarImage src={previewUrl ?? url ?? undefined} />}
          <AvatarFallback className="text-lg">{fallback}</AvatarFallback>
        </Avatar>
        {uploadsAllowed && (
          <div className="absolute inset-0 flex items-center justify-center rounded-full bg-black/50 opacity-0 transition-opacity group-hover:opacity-100">
            <Camera className="h-5 w-5 text-white" />
          </div>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/gif,image/webp"
        className="hidden"
        onChange={handleChange}
      />
      <div className="space-y-1">
        <div className="flex gap-1">
          {uploadsAllowed && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => inputRef.current?.click()}
              disabled={uploading}
            >
              {uploading ? "Uploading..." : "Upload"}
            </Button>
          )}
          {externalAllowed && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setShowUrlInput(!showUrlInput)}
              title="Use image URL"
            >
              <Link className="h-3 w-3" />
            </Button>
          )}
          {(previewUrl ?? url) && onRemove && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => { setPreviewUrl(null); onRemove(); }}
            >
              <X className="h-3 w-3" />
            </Button>
          )}
        </div>
        {showUrlInput && (
          <form onSubmit={handleUrlSubmit} className="flex gap-1">
            <Input
              value={urlValue}
              onChange={(e) => setUrlValue(e.target.value)}
              placeholder="https://..."
              className="h-7 text-xs"
              type="url"
              autoFocus
            />
            <Button type="submit" size="sm" variant="ghost" className="h-7 px-2 text-xs">
              Set
            </Button>
          </form>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </div>
      <AvatarCropperDialog
        open={pendingFile !== null}
        file={pendingFile}
        aspect={1}
        cropShape="round"
        outputMime="image/png"
        onConfirm={handleCroppedBlob}
        onCancel={() => setPendingFile(null)}
      />
    </div>
  );
}
