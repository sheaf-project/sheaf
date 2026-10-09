import { useState } from "react";
import { KeyRound } from "lucide-react";
import { useAuth } from "@/hooks/use-auth";
import { ApiError } from "@/lib/api-client";
import { isPasskeySupported, passkeyErrorMessage } from "@/lib/webauthn";
import { Button } from "@/components/ui/button";

interface Props {
  /** Captcha payload when the instance gates login with one; the passkey
   *  path is gated the same way the password form is. */
  captcha?: string;
  /** True while the instance wants a captcha the user has not solved yet. */
  captchaPending?: boolean;
  /** Surface a message under the form, the way the password form does. */
  onError: (message: string) => void;
}

/**
 * "Sign in with a passkey", below the password form.
 *
 * Never replaces or hides the password form: a passkey is an additional door,
 * and the account always keeps its password. Shown only when the instance
 * says passkeys are available (the caller checks the config) and the browser
 * has the API at all. Cancelling the platform sheet shows nothing; every
 * server refusal shows its own detail, which already points at the password.
 * An expired or reused challenge is retried once from the beginning, so
 * "start again" in the server's copy is true without the user doing it.
 */
export function PasskeySignInButton({ captcha, captchaPending, onError }: Props) {
  const { loginWithPasskey } = useAuth();
  const [busy, setBusy] = useState(false);

  if (!isPasskeySupported()) return null;

  async function attempt(retried: boolean): Promise<void> {
    try {
      await loginWithPasskey(captcha || undefined);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 400 && !retried && /expired or was already used/.test(err.detail)) {
          return attempt(true);
        }
        onError(err.detail);
        return;
      }
      const message = passkeyErrorMessage(err);
      if (message) onError(message);
    }
  }

  async function handleClick() {
    onError("");
    setBusy(true);
    try {
      await attempt(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="relative">
        <div className="absolute inset-0 flex items-center">
          <span className="w-full border-t" />
        </div>
        <div className="relative flex justify-center text-xs">
          <span className="bg-card px-2 text-muted-foreground">or</span>
        </div>
      </div>
      <Button
        type="button"
        variant="outline"
        className="w-full"
        onClick={handleClick}
        disabled={busy || captchaPending}
        data-testid="passkey-sign-in"
      >
        <KeyRound className="mr-2 h-4 w-4" />
        {busy ? "Waiting for your passkey..." : "Sign in with a passkey"}
      </Button>
    </div>
  );
}
