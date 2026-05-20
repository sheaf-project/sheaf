import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";
import type { DeleteConfirmation, DestructiveConfirm } from "@/types/api";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  /** Current auth tier from system settings — drives which fields to show. */
  tier: DeleteConfirmation;
  onConfirm: (confirm?: DestructiveConfirm) => void;
  loading?: boolean;
  /** Verb shown on the confirm button. Defaults to "Delete". */
  actionLabel?: string;
  /** Verb shown on the confirm button while loading. Defaults to "Deleting...". */
  actionLabelLoading?: string;
}

export function DestructiveConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  tier,
  onConfirm,
  loading,
  actionLabel = "Delete",
  actionLabelLoading = "Deleting...",
}: Props) {
  const { user } = useAuth();
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");

  function handleOpenChange(next: boolean) {
    if (!next) {
      setPassword("");
      setTotpCode("");
    }
    onOpenChange(next);
  }

  const needsPassword = tier === "password" || tier === "both";
  const needsTotp = (tier === "totp" || tier === "both") && !!user?.totp_enabled;

  function handleConfirm() {
    const confirm: DestructiveConfirm = {};
    if (needsPassword) confirm.password = password;
    if (needsTotp) confirm.totp_code = totpCode;
    onConfirm(Object.keys(confirm).length > 0 ? confirm : undefined);
  }

  const disabled =
    loading ||
    (needsPassword && !password) ||
    (needsTotp && !totpCode);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {(needsPassword || needsTotp) && (
          <div className="space-y-3">
            {needsPassword && (
              <div className="space-y-1">
                <Label htmlFor="destructive-confirm-password" className="text-sm">Password</Label>
                <Input
                  id="destructive-confirm-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                />
              </div>
            )}
            {needsTotp && (
              <div className="space-y-1">
                <Label htmlFor="destructive-confirm-totp" className="text-sm">TOTP code</Label>
                <Input
                  id="destructive-confirm-totp"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                  placeholder="6-digit code"
                  inputMode="numeric"
                  maxLength={6}
                  pattern="[0-9]{6}"
                  autoComplete="off"
                />
              </div>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleConfirm}
            disabled={disabled}
          >
            {loading ? actionLabelLoading : actionLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
