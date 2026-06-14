import { useCallback, useEffect, useState } from "react";
import Cropper from "react-easy-crop";
import type { Area } from "react-easy-crop";
import { RotateCcw, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";

// Longest-edge cap for the cropper output. Server normalization caps at
// settings.max_image_dimension (default 4096); going smaller here keeps
// the upload payload sensible without losing user-visible quality.
const MAX_OUTPUT_DIM = 1024;

const clampRotation = (v: number): number => Math.max(-180, Math.min(180, v));

// Quarter-turn buttons: jump to the next multiple of 90 in each direction.
// The +/-1 nudge means landing exactly on a multiple advances to the next one
// rather than sticking. The slider itself stays free for fine adjustment.
const rotateToPrev90 = (r: number): number =>
  clampRotation(Math.floor((r - 1) / 90) * 90);
const rotateToNext90 = (r: number): number =>
  clampRotation(Math.ceil((r + 1) / 90) * 90);

interface AvatarCropperDialogProps {
  open: boolean;
  file: File | null;
  aspect?: number;          // 1 for avatars; undefined => freeform
  cropShape?: "round" | "rect";
  outputMime?: "image/png" | "image/jpeg" | "image/webp";
  // Smallest zoom (1 = "cover" the frame). Below 1 the image can shrink
  // inside the frame, letterboxing, so the crop can include the whole image.
  // Defaults below 1 so any crop (round avatar, rect embed, banner) can zoom
  // out to fit the whole image rather than forcing the corners/edges off.
  minZoom?: number;
  // When false, the image can be panned so the crop frame reaches past its
  // edges (the overflow renders transparent). Off by default so a crop can go
  // right to the edge of an image whose ratio doesn't match the frame.
  restrictPosition?: boolean;
  onConfirm: (cropped: Blob) => void;
  onCancel: () => void;
}

export function AvatarCropperDialog({
  open,
  file,
  aspect = 1,
  cropShape = "round",
  outputMime = "image/png",
  minZoom = 0.1,
  restrictPosition = false,
  onConfirm,
  onCancel,
}: AvatarCropperDialogProps) {
  const [imageSrc, setImageSrc] = useState<string | null>(null);
  const [crop, setCrop] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [croppedAreaPixels, setCroppedAreaPixels] = useState<Area | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  // (Re)load the source as an object URL whenever the file changes.
  // Without this, picking the same file twice in a row would not re-fire
  // the <Cropper> with a fresh src.
  useEffect(() => {
    if (!file) {
      setImageSrc(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setImageSrc(url);
    setCrop({ x: 0, y: 0 });
    setZoom(1);
    setRotation(0);
    setCroppedAreaPixels(null);
    setError(null);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const onCropComplete = useCallback(
    (_area: Area, areaPixels: Area) => setCroppedAreaPixels(areaPixels),
    [],
  );

  const handleConfirm = useCallback(async () => {
    if (!imageSrc || !croppedAreaPixels) return;
    setWorking(true);
    setError(null);
    try {
      const blob = await renderCroppedBlob(
        imageSrc,
        croppedAreaPixels,
        rotation,
        outputMime,
      );
      onConfirm(blob);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not crop image");
    } finally {
      setWorking(false);
    }
  }, [imageSrc, croppedAreaPixels, rotation, outputMime, onConfirm]);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {cropShape === "round" ? "Crop avatar" : "Crop image"}
          </DialogTitle>
          <DialogDescription>
            {cropShape === "round"
              ? "Drag and zoom to choose what shows inside the circle."
              : "Drag and zoom to choose the region you want to keep."}
          </DialogDescription>
        </DialogHeader>

        {imageSrc && (
          <div className="relative h-72 w-full overflow-hidden rounded-md bg-muted">
            <Cropper
              image={imageSrc}
              crop={crop}
              zoom={zoom}
              rotation={rotation}
              aspect={aspect}
              cropShape={cropShape}
              showGrid={cropShape === "rect"}
              minZoom={minZoom}
              restrictPosition={restrictPosition}
              onCropChange={setCrop}
              onCropComplete={onCropComplete}
              onZoomChange={setZoom}
              onRotationChange={setRotation}
            />
          </div>
        )}

        <div className="space-y-3 pt-2">
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Zoom</Label>
            <input
              type="range"
              min={minZoom}
              max={4}
              step={0.01}
              value={zoom}
              onChange={(e) => setZoom(Number(e.target.value))}
              className="w-full accent-primary"
              aria-label="Zoom"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Rotation</Label>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="icon"
                className="h-7 w-7 shrink-0"
                onClick={() => setRotation(rotateToPrev90(rotation))}
                title="Rotate 90 left"
                aria-label="Rotate 90 degrees left"
              >
                <RotateCcw className="h-3.5 w-3.5" />
              </Button>
              <input
                type="range"
                min={-180}
                max={180}
                step={1}
                value={rotation}
                onChange={(e) => setRotation(Number(e.target.value))}
                className="w-full accent-primary"
                aria-label="Rotation"
              />
              <Button
                type="button"
                variant="outline"
                size="icon"
                className="h-7 w-7 shrink-0"
                onClick={() => setRotation(rotateToNext90(rotation))}
                title="Rotate 90 right"
                aria-label="Rotate 90 degrees right"
              >
                <RotateCw className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </div>

        {error && <p className="text-xs text-destructive">{error}</p>}

        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={onCancel}
            disabled={working}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={handleConfirm}
            disabled={working || !croppedAreaPixels}
          >
            {working ? "Cropping..." : "Use this crop"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Render the cropped/rotated region onto a canvas and return a Blob.
// Bounded by MAX_OUTPUT_DIM to keep the uploaded payload sensible; the
// server-side normalizer downscales again if needed.
async function renderCroppedBlob(
  imageSrc: string,
  area: Area,
  rotation: number,
  mime: "image/png" | "image/jpeg" | "image/webp",
): Promise<Blob> {
  const image = await loadImage(imageSrc);

  // Output canvas, capped to MAX_OUTPUT_DIM on the longest edge.
  const longest = Math.max(area.width, area.height);
  const scale = longest > MAX_OUTPUT_DIM ? MAX_OUTPUT_DIM / longest : 1;
  const outW = Math.max(1, Math.round(area.width * scale));
  const outH = Math.max(1, Math.round(area.height * scale));

  const canvas = document.createElement("canvas");
  canvas.width = outW;
  canvas.height = outH;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas context unavailable");

  if (rotation === 0) {
    // Fast path: straight crop, no rotation matrix.
    ctx.drawImage(
      image,
      area.x,
      area.y,
      area.width,
      area.height,
      0,
      0,
      outW,
      outH,
    );
  } else {
    // Rotate around the source-image centre, then translate so the
    // requested crop area lands at the canvas origin. This matches
    // react-easy-crop's own coordinate conventions (rotation around the
    // image centre, then crop in the rotated frame).
    const safeArea =
      2 *
      Math.ceil(
        (Math.max(image.width, image.height) / 2) * Math.sqrt(2),
      );
    const rotCanvas = document.createElement("canvas");
    rotCanvas.width = safeArea;
    rotCanvas.height = safeArea;
    const rotCtx = rotCanvas.getContext("2d");
    if (!rotCtx) throw new Error("canvas context unavailable");
    rotCtx.translate(safeArea / 2, safeArea / 2);
    rotCtx.rotate((rotation * Math.PI) / 180);
    rotCtx.translate(-safeArea / 2, -safeArea / 2);
    rotCtx.drawImage(
      image,
      safeArea / 2 - image.width / 2,
      safeArea / 2 - image.height / 2,
    );

    const data = rotCtx.getImageData(0, 0, safeArea, safeArea);
    canvas.width = outW;
    canvas.height = outH;
    ctx.putImageData(
      data,
      Math.round(-safeArea / 2 + image.width / 2 - area.x),
      Math.round(-safeArea / 2 + image.height / 2 - area.y),
    );
    // The putImageData path bypasses scaling, so re-draw scaled if needed.
    if (scale !== 1) {
      const tmp = document.createElement("canvas");
      tmp.width = area.width;
      tmp.height = area.height;
      const tctx = tmp.getContext("2d");
      if (!tctx) throw new Error("canvas context unavailable");
      tctx.putImageData(
        data,
        Math.round(-safeArea / 2 + image.width / 2 - area.x),
        Math.round(-safeArea / 2 + image.height / 2 - area.y),
      );
      canvas.width = outW;
      canvas.height = outH;
      ctx.drawImage(tmp, 0, 0, area.width, area.height, 0, 0, outW, outH);
    }
  }

  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("toBlob failed"))),
      mime,
      mime === "image/jpeg" ? 0.9 : undefined,
    );
  });
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("could not load image"));
    img.src = src;
  });
}
