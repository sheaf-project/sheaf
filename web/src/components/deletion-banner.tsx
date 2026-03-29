import { useState } from "react";
import { useAuth } from "@/hooks/use-auth";
import { cancelDeletion } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { AlertTriangle } from "lucide-react";

export function DeletionBanner() {
  const { user, refreshUser } = useAuth();
  const [cancelling, setCancelling] = useState(false);

  if (!user?.deletion_requested_at) return null;

  const deletionDate = new Date(user.deletion_requested_at);
  // Grace period is server-configured; we approximate from the date
  // The exact date doesn't matter for display — we show relative time
  const now = new Date();
  const daysRemaining = Math.max(
    0,
    Math.ceil(
      (deletionDate.getTime() + 14 * 86400000 - now.getTime()) / 86400000,
    ),
  );

  async function handleCancel() {
    setCancelling(true);
    try {
      await cancelDeletion();
      await refreshUser();
    } catch {
      // Error toast handled by apiFetch
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="flex items-center gap-3 bg-destructive/10 border-b border-destructive/20 px-4 py-2 text-sm">
      <AlertTriangle className="h-4 w-4 text-destructive shrink-0" />
      <span className="text-destructive">
        Your account is scheduled for deletion
        {daysRemaining > 0 ? ` in ${daysRemaining} day${daysRemaining === 1 ? "" : "s"}` : " soon"}.
      </span>
      <Button
        variant="outline"
        size="sm"
        className="ml-auto h-7 text-xs"
        onClick={handleCancel}
        disabled={cancelling}
      >
        {cancelling ? "Cancelling..." : "Cancel deletion"}
      </Button>
    </div>
  );
}
