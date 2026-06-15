import { type FormEvent, useRef, useState } from "react";
import { toast } from "sonner";
import { uploadFile } from "@/lib/files";
import { apiErrorMessage } from "@/lib/api-errors";
import { AvatarCropperDialog } from "@/components/avatar-cropper-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Camera, X, Link, Image as ImageIcon } from "lucide-react";
import { useAuth } from "@/hooks/use-auth";

// Wide header image for a member profile. Crops to a fixed 3:1 banner shape,
// but unlike the avatar the crop can zoom out and pan past the image edges
// (minZoom < 1 + restrictPosition off), so an image whose ratio isn't 3:1 can
// still be used whole (letterboxed) rather than forcing the sides off.
export function BannerUpload({
  url,
  onUpload,
  onRemove,
}: {
  url: string | null;
  onUpload: (key: string) => void;
  onRemove?: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [showUrlInput, setShowUrlInput] = useState(false);
  const [urlValue, setUrlValue] = useState("");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const { user } = useAuth();
  const uploadsAllowed = user?.uploads_allowed ?? true;
  const externalAllowed = user?.external_images_allowed ?? true;
  const shown = previewUrl ?? url;

  async function handleCroppedBlob(blob: Blob) {
    setPendingFile(null);
    setError("");
    setUploading(true);
    try {
      const named = new File([blob], "banner.png", { type: blob.type });
      const res = await uploadFile(named, "banner");
      setPreviewUrl(res.url);
      onUpload(res.key);
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
    // Nested <form>; stop the submit bubbling to the parent profile form
    // (see AvatarUpload for the full rationale).
    e.stopPropagation();
    const trimmed = urlValue.trim();
    if (!trimmed) return;
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
    <div className="space-y-1">
      <div
        className={`group relative aspect-[3/1] w-full overflow-hidden rounded-md border bg-muted ${
          uploadsAllowed ? "cursor-pointer" : ""
        }`}
        onClick={uploadsAllowed ? () => inputRef.current?.click() : undefined}
        onDrop={uploadsAllowed ? handleDrop : undefined}
        onDragOver={uploadsAllowed ? (e) => e.preventDefault() : undefined}
      >
        {shown ? (
          <img src={shown} alt="" className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-muted-foreground">
            <ImageIcon className="h-6 w-6" />
          </div>
        )}
        {uploadsAllowed && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/50 opacity-0 transition-opacity group-hover:opacity-100">
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
        {shown && onRemove && (
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
      <AvatarCropperDialog
        open={pendingFile !== null}
        file={pendingFile}
        aspect={3}
        cropShape="rect"
        outputMime="image/png"
        onConfirm={handleCroppedBlob}
        onCancel={() => setPendingFile(null)}
      />
    </div>
  );
}
