import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { useAuth } from "@/hooks/use-auth";
import { revalidateEmail } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { AlertTriangle } from "lucide-react";

// Shown when the account's email has been flagged undeliverable. Without
// this prompt a bounced/complained address is a silent, admin-only
// lockout: the user stops receiving security and account mail and has no
// way to know or fix it. Re-verifying the address (or changing it)
// clears the flag server-side; the verification send is forced so it
// reaches even a currently-blocked address.
export function EmailDeliverabilityBanner() {
  const { user, refreshUser } = useAuth();
  const [sending, setSending] = useState(false);

  if (!user) return null;
  const flagged =
    user.email_revalidation_required || user.email_delivery_status !== "ok";
  if (!flagged) return null;

  // A complaint (recipient marked us as spam) is a stronger signal than a
  // bounce, but the user-facing action is identical, so the copy only
  // distinguishes "we couldn't deliver" from neutral phrasing.
  const complained = user.email_delivery_status === "complained";

  async function handleResend() {
    setSending(true);
    try {
      await revalidateEmail();
      toast.success(
        "Verification email sent. Open the link in it to restore delivery.",
      );
      await refreshUser();
    } catch {
      // Error toast handled by apiFetch
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3 bg-destructive/10 border-b border-destructive/20 px-4 py-2 text-sm">
      <AlertTriangle className="h-4 w-4 text-destructive shrink-0" />
      <span className="text-destructive">
        {complained
          ? "Email to your address was marked as spam, so we've stopped sending to it."
          : "We couldn't deliver email to your address."}{" "}
        Re-verify or change it so you don't miss security and account
        notifications.
      </span>
      <div className="ml-auto flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          onClick={handleResend}
          disabled={sending}
        >
          {sending ? "Sending..." : "Re-send verification"}
        </Button>
        <Button asChild variant="ghost" size="sm" className="h-7 text-xs">
          <Link to="/settings/account">Change email</Link>
        </Button>
      </div>
    </div>
  );
}
