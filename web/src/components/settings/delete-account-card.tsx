import { type FormEvent, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/hooks/use-auth";
import { requestAccountDeletion, cancelDeletion, getAuthConfig } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { AlertTriangle } from "lucide-react";
import { ApiError } from "@/lib/api-client";

export function DeleteAccountCard() {
  const { user, refreshUser } = useAuth();
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  const { data: config } = useQuery({ queryKey: ["auth-config"], queryFn: getAuthConfig });
  const isPending = user?.account_status === "pending_deletion";
  const graceDays = config?.account_deletion_grace_days ?? 7;

  async function handleDelete(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await requestAccountDeletion(password, totpCode || undefined);
      await refreshUser();
      setPassword("");
      setTotpCode("");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        setError("Something went wrong");
      }
    } finally {
      setSubmitting(false);
    }
  }

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

  if (isPending) {
    const deletionDate = user?.deletion_scheduled_for
      ? new Date(user.deletion_scheduled_for)
      : null;

    return (
      <Card className="border-destructive/50">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-4 w-4" />
            Account deletion scheduled
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Your account is scheduled for permanent deletion
            {deletionDate
              ? ` on ${deletionDate.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })}`
              : ""}.
            All your data will be permanently removed.
          </p>
          <Button
            variant="outline"
            onClick={handleCancel}
            disabled={cancelling}
          >
            {cancelling ? "Cancelling..." : "Cancel deletion"}
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-destructive/50">
      <CardHeader>
        <CardTitle className="text-base text-destructive">
          Delete account
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground mb-4">
          Permanently delete your account and all associated data. You will have
          a {graceDays}-day grace period to change your mind.
        </p>
        <form onSubmit={handleDelete} className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="delete-password">Confirm your password</Label>
            <Input
              id="delete-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
            />
          </div>
          {user?.totp_enabled && (
            <div className="space-y-2">
              <Label htmlFor="delete-totp">2FA code</Label>
              <Input
                id="delete-totp"
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                placeholder="Enter TOTP or recovery code"
                autoComplete="one-time-code"
              />
            </div>
          )}
          {error && (
            <p className="text-sm text-destructive-foreground">{error}</p>
          )}
          <Button
            type="submit"
            variant="destructive"
            disabled={submitting || !password}
          >
            {submitting ? "Requesting deletion..." : "Delete my account"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
