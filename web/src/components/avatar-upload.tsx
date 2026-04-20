import { type FormEvent, useRef, useState } from "react";
import { uploadFile } from "@/lib/files";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
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
  const { user } = useAuth();
  const uploadsAllowed = user?.uploads_allowed ?? true;

  async function handleFile(file: File) {
    setError("");
    setUploading(true);
    try {
      const res = await uploadFile(file);
      setPreviewUrl(res.url); // show signed URL immediately
      onUpload(res.key);      // store the key, not the expiring URL
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    e.target.value = "";
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith("image/")) handleFile(file);
  }

  function handleUrlSubmit(e: FormEvent) {
    e.preventDefault();
    if (urlValue.trim()) {
      onUpload(urlValue.trim());
      setUrlValue("");
      setShowUrlInput(false);
    }
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
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setShowUrlInput(!showUrlInput)}
            title="Use image URL"
          >
            <Link className="h-3 w-3" />
          </Button>
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
    </div>
  );
}
