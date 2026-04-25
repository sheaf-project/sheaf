import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { useAuth } from "@/hooks/use-auth";
import { changeEmail } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function ChangeEmail() {
  const { user, refreshUser } = useAuth();
  const [open, setOpen] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  function reset() {
    setNewEmail("");
    setCurrentPassword("");
    setTotpCode("");
    setError("");
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (newEmail.trim().toLowerCase() === user?.email?.trim().toLowerCase()) {
      setError("New email must differ from the current email");
      return;
    }
    setLoading(true);
    try {
      const res = await changeEmail(
        newEmail,
        currentPassword,
        user?.totp_enabled ? totpCode : undefined,
      );
      reset();
      setOpen(false);
      await refreshUser();
      const parts: string[] = [];
      if (res.verification_sent) {
        parts.push("Check the new address for a verification link.");
      }
      if (res.revoked_other_sessions > 0) {
        parts.push(`${res.revoked_other_sessions} other session(s) signed out.`);
      }
      toast.success(`Email changed to ${res.email}.${parts.length ? " " + parts.join(" ") : ""}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Change failed");
    } finally {
      setLoading(false);
    }
  }

  if (!open) {
    return (
      <Button variant="outline" onClick={() => setOpen(true)}>
        Change email
      </Button>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="new-email">New email address</Label>
        <Input
          id="new-email"
          type="email"
          autoComplete="email"
          value={newEmail}
          onChange={(e) => setNewEmail(e.target.value)}
          required
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="email-current-password">Current password</Label>
        <Input
          id="email-current-password"
          type="password"
          autoComplete="current-password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          required
        />
      </div>
      {user?.totp_enabled && (
        <div className="space-y-1.5">
          <Label htmlFor="email-totp-code">TOTP or recovery code</Label>
          <Input
            id="email-totp-code"
            type="text"
            inputMode="text"
            autoComplete="one-time-code"
            value={totpCode}
            onChange={(e) => setTotpCode(e.target.value)}
            required
          />
        </div>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      <p className="text-xs text-muted-foreground">
        You'll need to verify the new address. All other sessions on this account will be signed out.
      </p>
      <div className="flex gap-2">
        <Button type="submit" disabled={loading}>
          {loading ? "Changing..." : "Change email"}
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            reset();
            setOpen(false);
          }}
          disabled={loading}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}
