import { type FormEvent, useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { useAuth } from "@/hooks/use-auth";
import {
  totpSetup,
  totpVerify,
  totpDisable,
  regenerateRecoveryCodes,
  type TOTPSetupResponse,
} from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Step = "idle" | "qr" | "recovery" | "done";

export function TOTPSetup() {
  const { user, refreshUser } = useAuth();
  const [step, setStep] = useState<Step>("idle");
  const [setup, setSetup] = useState<TOTPSetupResponse | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleBeginSetup() {
    setError("");
    setLoading(true);
    try {
      const data = await totpSetup();
      setSetup(data);
      setStep("qr");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Setup failed");
    } finally {
      setLoading(false);
    }
  }

  // Don't leave the recovery codes rendered indefinitely if the user
  // walks away mid-setup. Five minutes is plenty to save them; after that
  // we drop back to idle (codes can be regenerated if missed).
  useEffect(() => {
    if (step !== "recovery") return;
    const timer = setTimeout(() => {
      setStep("idle");
      setSetup(null);
      setCode("");
    }, 300_000);
    return () => clearTimeout(timer);
  }, [step]);

  async function handleVerify(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await totpVerify(code);
      await refreshUser();
      setStep("recovery");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verification failed");
    } finally {
      setLoading(false);
    }
  }

  if (user?.totp_enabled && step === "idle") {
    return <TOTPDisable />;
  }

  if (step === "idle") {
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">
          Add an extra layer of security with a TOTP authenticator app.
        </p>
        <Button onClick={handleBeginSetup} disabled={loading}>
          {loading ? "Setting up..." : "Enable 2FA"}
        </Button>
        {error && <p className="text-sm text-destructive">{error}</p>}
      </div>
    );
  }

  if (step === "qr" && setup) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Scan this QR code with your authenticator app, then enter the 6-digit code to confirm.
        </p>
        <div className="flex justify-center rounded-lg bg-white p-4 w-fit mx-auto">
          <QRCodeSVG value={setup.provisioning_uri} size={200} />
        </div>
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">
            Can't scan? Enter manually
          </summary>
          <code className="mt-1 block break-all rounded bg-muted p-2 text-xs">
            {setup.secret}
          </code>
        </details>
        <form onSubmit={handleVerify} className="space-y-2">
          <Label htmlFor="totp-setup-verify">Verification code</Label>
          <div className="flex gap-2">
            <Input
              id="totp-setup-verify"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="000000"
              inputMode="numeric"
              maxLength={6}
              pattern="[0-9]{6}"
              required
              className="w-32"
              autoComplete="off"
              autoFocus
            />
            <Button type="submit" disabled={loading || code.length !== 6}>
              {loading ? "Verifying..." : "Verify"}
            </Button>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </form>
      </div>
    );
  }

  if (step === "recovery" && setup) {
    return (
      <div className="space-y-4">
        <p className="text-sm font-medium text-green-600">
          2FA is now enabled.
        </p>
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            Save these recovery codes somewhere safe. Each code can only be used once.
            If you lose access to your authenticator, these are the only way to regain access.
          </p>
          <div className="grid grid-cols-2 gap-1 rounded-lg border bg-muted/50 p-3">
            {setup.recovery_codes.map((c) => (
              <code key={c} className="text-sm font-mono">{c}</code>
            ))}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              navigator.clipboard.writeText(setup.recovery_codes.join("\n"));
            }}
          >
            Copy to clipboard
          </Button>
        </div>
        <Button onClick={() => { setStep("idle"); setSetup(null); setCode(""); }}>
          Done
        </Button>
      </div>
    );
  }

  return null;
}

function TOTPDisable() {
  const { user, refreshUser } = useAuth();
  const [action, setAction] = useState<"idle" | "disable" | "regenerate" | "show-codes">("idle");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [newCodes, setNewCodes] = useState<string[]>([]);

  // Mirror the setup flow: don't leave regenerated recovery codes on
  // screen forever if the user steps away.
  useEffect(() => {
    if (action !== "show-codes") return;
    const timer = setTimeout(() => {
      setAction("idle");
      setNewCodes([]);
    }, 300_000);
    return () => clearTimeout(timer);
  }, [action]);

  async function handleDisable(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await totpDisable(user!.email, password, totpCode);
      await refreshUser();
      setAction("idle");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to disable 2FA");
    } finally {
      setLoading(false);
    }
  }

  async function handleRegenerate(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await regenerateRecoveryCodes(totpCode);
      setNewCodes(result.recovery_codes);
      setAction("show-codes");
      setTotpCode("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to regenerate codes");
    } finally {
      setLoading(false);
    }
  }

  if (action === "show-codes") {
    return (
      <div className="space-y-4">
        <p className="text-sm font-medium text-green-600">
          Recovery codes regenerated.
        </p>
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            Save these new recovery codes somewhere safe. Your old codes are now invalid.
          </p>
          <div className="grid grid-cols-2 gap-1 rounded-lg border bg-muted/50 p-3">
            {newCodes.map((c) => (
              <code key={c} className="text-sm font-mono">{c}</code>
            ))}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              navigator.clipboard.writeText(newCodes.join("\n"));
            }}
          >
            Copy to clipboard
          </Button>
        </div>
        <Button onClick={() => { setAction("idle"); setNewCodes([]); }}>
          Done
        </Button>
      </div>
    );
  }

  if (action === "idle") {
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">
          2FA is enabled. Your account is protected with a TOTP authenticator.
        </p>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setAction("regenerate")}>
            Regenerate recovery codes
          </Button>
          <Button variant="outline" onClick={() => setAction("disable")}>
            Disable 2FA
          </Button>
        </div>
      </div>
    );
  }

  if (action === "regenerate") {
    return (
      <form onSubmit={handleRegenerate} className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Enter a current TOTP code to generate new recovery codes. This will invalidate your existing codes.
        </p>
        <div className="space-y-1">
          <Label htmlFor="totp-regen-code" className="text-sm">TOTP code</Label>
          <Input
            id="totp-regen-code"
            value={totpCode}
            onChange={(e) => setTotpCode(e.target.value)}
            placeholder="000000"
            inputMode="numeric"
            maxLength={6}
            pattern="[0-9]{6}"
            autoComplete="off"
            required
            autoFocus
          />
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex gap-2">
          <Button type="submit" disabled={loading || totpCode.length !== 6}>
            {loading ? "Regenerating..." : "Regenerate codes"}
          </Button>
          <Button type="button" variant="outline" onClick={() => { setAction("idle"); setError(""); setTotpCode(""); }}>
            Cancel
          </Button>
        </div>
      </form>
    );
  }

  return (
    <form onSubmit={handleDisable} className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Enter your password and a current TOTP code to disable 2FA.
      </p>
      <div className="space-y-1">
        <Label htmlFor="totp-disable-password" className="text-sm">Password</Label>
        <Input
          id="totp-disable-password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor="totp-disable-code" className="text-sm">TOTP code</Label>
        <Input
          id="totp-disable-code"
          value={totpCode}
          onChange={(e) => setTotpCode(e.target.value)}
          placeholder="000000"
          inputMode="numeric"
          maxLength={6}
          pattern="[0-9]{6}"
          autoComplete="off"
          required
        />
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <div className="flex gap-2">
        <Button type="submit" variant="destructive" disabled={loading}>
          {loading ? "Disabling..." : "Disable 2FA"}
        </Button>
        <Button type="button" variant="outline" onClick={() => { setAction("idle"); setError(""); setPassword(""); setTotpCode(""); }}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
