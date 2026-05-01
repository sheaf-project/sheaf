import { Copy, ExternalLink } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function ActivationLinkModal({
  open,
  onOpenChange,
  url,
  expiresAt,
  channelName,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  url: string | null;
  expiresAt: string | null;
  channelName: string;
}) {
  if (!url) return null;
  const expires = expiresAt ? new Date(expiresAt) : null;

  function copy() {
    if (!url) return;
    navigator.clipboard.writeText(url).then(
      () => toast.success("Copied"),
      () => toast.error("Couldn't copy - select and copy manually"),
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Send this to {channelName}</DialogTitle>
          <DialogDescription>
            One-time activation link. We won't show it again, so copy it now and
            relay it to the recipient out-of-band (chat, signal, in-person).
            {expires && (
              <>
                {" "}Expires <strong>{expires.toLocaleString()}</strong>.
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="rounded border bg-muted px-3 py-2 text-xs break-all font-mono">
          {url}
        </div>

        <DialogFooter className="flex flex-row gap-2 sm:justify-between">
          <Button type="button" variant="outline" onClick={copy}>
            <Copy className="mr-2 h-4 w-4" /> Copy link
          </Button>
          <Button asChild type="button">
            <a href={url} target="_blank" rel="noopener noreferrer">
              <ExternalLink className="mr-2 h-4 w-4" /> Open
            </a>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
