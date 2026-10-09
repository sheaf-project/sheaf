import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Pencil } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/hooks/use-auth";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import {
  deletePasskey,
  getAuthConfig,
  listPasskeys,
  passkeyRegisterBegin,
  passkeyRegisterComplete,
  renamePasskey,
  type Passkey,
} from "@/lib/auth";
import { apiErrorMessage } from "@/lib/api-errors";
import { createPasskey, isPasskeySupported, passkeyErrorMessage } from "@/lib/webauthn";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const PASSKEYS_KEY = ["passkeys"];

/**
 * Settings > Account > Passkeys: the list, add, rename and remove.
 *
 * Rules from the passkey design, all of them enforced server-side and only
 * reflected here: adding asks for the password plus a TOTP or recovery code
 * when the account has TOTP; removing asks for nothing and the last one may
 * go, because the password always remains; a row created under another
 * address stays listed and says why it no longer works. The whole card is
 * absent when the instance does not offer passkeys, matching how the
 * mobile-push picker hides, with no explanatory stub.
 */
export function PasskeysCard() {
  const { data: config } = useQuery({
    queryKey: ["auth-config"],
    queryFn: getAuthConfig,
    staleTime: 5 * 60_000,
  });
  const available = config?.passkeys_available === true;
  const { data: passkeys } = useQuery({
    queryKey: PASSKEYS_KEY,
    queryFn: listPasskeys,
    enabled: available,
  });

  if (!available || !isPasskeySupported()) return null;

  return (
    <Card data-testid="passkeys-card">
      <CardHeader>
        <CardTitle className="text-base">Passkeys</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <p className="text-xs text-muted-foreground">
          Sign in with a tap using this device, a password manager, or a
          hardware key. Your password always keeps working.
        </p>
        {passkeys && passkeys.length === 0 && (
          <p className="text-sm text-muted-foreground">No passkeys yet.</p>
        )}
        {passkeys?.map((p) => <PasskeyRow key={p.id} passkey={p} />)}
        <AddPasskey />
      </CardContent>
    </Card>
  );
}

function PasskeyRow({ passkey: p }: { passkey: Passkey }) {
  const qc = useQueryClient();
  const { formatDate } = useDateFormatters();
  const [editing, setEditing] = useState(false);
  const [nickname, setNickname] = useState(p.nickname ?? "");
  const [confirmingRemove, setConfirmingRemove] = useState(false);

  const rename = useMutation({
    mutationFn: (name: string) => renamePasskey(p.id, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: PASSKEYS_KEY });
      toast.success("Passkey renamed");
    },
  });
  const remove = useMutation({
    mutationFn: () => deletePasskey(p.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: PASSKEYS_KEY });
      toast.success("Passkey removed");
    },
  });

  function saveNickname() {
    setEditing(false);
    if ((nickname.trim() || "") !== (p.nickname ?? "")) {
      rename.mutate(nickname.trim());
    }
  }

  return (
    <div className="rounded-md border px-3 py-2 text-sm space-y-1" data-testid="passkey-row">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            {editing ? (
              <form
                className="flex items-center gap-1"
                onSubmit={(e) => {
                  e.preventDefault();
                  saveNickname();
                }}
              >
                <Input
                  value={nickname}
                  onChange={(e) => setNickname(e.target.value)}
                  className="h-6 w-44 text-xs"
                  placeholder="Name this passkey"
                  maxLength={128}
                  autoFocus
                  onBlur={saveNickname}
                />
              </form>
            ) : (
              <button
                type="button"
                className="inline-flex items-center gap-1 font-medium hover:text-muted-foreground transition-colors"
                onClick={() => {
                  setNickname(p.nickname ?? "");
                  setEditing(true);
                }}
                aria-label="Rename passkey"
              >
                <KeyRound className="h-3.5 w-3.5 text-muted-foreground" />
                {p.nickname || "Unnamed passkey"}
                <Pencil className="h-3 w-3 text-muted-foreground" />
              </button>
            )}
            <Badge variant="outline" className="text-xs">
              {p.backup_eligible ? "Synced passkey" : "Device or hardware key"}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            Added {formatDate(p.created_at)}
            {" · "}
            {p.last_used_at ? `Last used ${formatDate(p.last_used_at)}` : "Never used"}
          </p>
          {!p.usable && (
            <p className="text-xs text-destructive">
              Created for {p.rp_id}, which is not this instance's current
              address. It cannot be used to sign in; remove it and add a new
              one.
            </p>
          )}
        </div>
        {!confirmingRemove && (
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:text-destructive shrink-0"
            onClick={() => setConfirmingRemove(true)}
            disabled={remove.isPending}
          >
            Remove
          </Button>
        )}
      </div>
      {confirmingRemove && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded bg-muted/40 px-2 py-1.5">
          <span className="text-xs">
            Remove this passkey? You can still sign in with your password.
          </span>
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="destructive"
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
            >
              {remove.isPending ? "Removing..." : "Remove"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setConfirmingRemove(false)}>
              Keep
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

type AddStep = "idle" | "step-up" | "ceremony" | "name";

function AddPasskey() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [step, setStep] = useState<AddStep>("idle");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [nickname, setNickname] = useState("");
  const [credential, setCredential] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  function reset() {
    setStep("idle");
    setPassword("");
    setTotpCode("");
    setNickname("");
    setCredential(null);
    setError("");
    setLoading(false);
  }

  async function handleStepUp(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const { options } = await passkeyRegisterBegin(
        password,
        user?.totp_enabled ? totpCode : undefined,
      );
      setPassword("");
      setTotpCode("");
      setStep("ceremony");
      // The platform sheet is up from here; the step-up is spent either way.
      const cred = await createPasskey(options);
      setCredential(cred);
      setStep("name");
    } catch (err) {
      const platform = passkeyErrorMessage(err);
      if (err instanceof DOMException || platform === null) {
        // Cancelled or a platform refusal: back to the beginning, with the
        // platform's one sentence when there is one, and nothing when the
        // user simply closed the sheet.
        setStep("idle");
        if (platform) setError(platform);
      } else {
        setStep("step-up");
        setError(apiErrorMessage(err, "Could not start adding a passkey"));
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleName(e: FormEvent) {
    e.preventDefault();
    if (!credential) return;
    setError("");
    setLoading(true);
    try {
      await passkeyRegisterComplete(credential, nickname.trim() || undefined);
      qc.invalidateQueries({ queryKey: PASSKEYS_KEY });
      toast.success("Passkey added");
      reset();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not save the passkey", { preferDetail: true }));
      setLoading(false);
    }
  }

  if (step === "idle") {
    return (
      <div className="space-y-2">
        <Button variant="outline" onClick={() => { setError(""); setStep("step-up"); }}>
          Add passkey
        </Button>
        <p className="text-xs text-muted-foreground">
          Hardware keys have a limited number of slots; synced passkeys do not.
        </p>
        {error && <p className="text-sm text-destructive">{error}</p>}
      </div>
    );
  }

  if (step === "step-up" || step === "ceremony") {
    return (
      <form onSubmit={handleStepUp} className="space-y-3 rounded-md border p-3">
        <p className="text-sm text-muted-foreground">
          Confirm your password
          {user?.totp_enabled ? " and a two-factor code" : ""} to add a passkey.
        </p>
        <div className="space-y-1">
          <Label htmlFor="passkey-add-password" className="text-sm">Password</Label>
          <Input
            id="passkey-add-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoFocus
            disabled={step === "ceremony"}
          />
        </div>
        {user?.totp_enabled && (
          <div className="space-y-1">
            <Label htmlFor="passkey-add-totp" className="text-sm">TOTP or recovery code</Label>
            <Input
              id="passkey-add-totp"
              autoComplete="one-time-code"
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
              required
              disabled={step === "ceremony"}
            />
          </div>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex gap-2">
          <Button type="submit" disabled={loading || !password || (user?.totp_enabled && !totpCode)}>
            {step === "ceremony" ? "Waiting for your passkey..." : loading ? "Checking..." : "Continue"}
          </Button>
          <Button type="button" variant="outline" onClick={reset} disabled={step === "ceremony"}>
            Cancel
          </Button>
        </div>
      </form>
    );
  }

  return (
    <form onSubmit={handleName} className="space-y-3 rounded-md border p-3">
      <p className="text-sm text-muted-foreground">
        Your passkey is ready. Give it a name so you can tell it apart later.
      </p>
      <div className="space-y-1">
        <Label htmlFor="passkey-add-nickname" className="text-sm">
          Name this passkey (optional)
        </Label>
        <Input
          id="passkey-add-nickname"
          value={nickname}
          onChange={(e) => setNickname(e.target.value)}
          placeholder="e.g. Phone, Blue YubiKey"
          maxLength={128}
          autoFocus
        />
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <div className="flex gap-2">
        <Button type="submit" disabled={loading}>
          {loading ? "Saving..." : "Save passkey"}
        </Button>
      </div>
    </form>
  );
}
