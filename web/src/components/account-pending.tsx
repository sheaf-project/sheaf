import { useState, useEffect, useCallback } from "react";
import { useAuth } from "@/hooks/use-auth";
import { resendVerification, verifyEmail } from "@/lib/auth";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Mail, Clock, Check, LogOut } from "lucide-react";

export function AccountPending() {
  const { user, logout, refreshUser } = useAuth();
  const [resendState, setResendState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [resendError, setResendError] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [verifyState, setVerifyState] = useState<"idle" | "verifying" | "error">("idle");
  const [verifyError, setVerifyError] = useState<string | null>(null);

  const needsVerification = !user?.email_verified;
  const needsApproval = user?.account_status === "pending_approval";

  // Poll for status changes every 15 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      refreshUser().catch(() => {});
    }, 15000);
    return () => clearInterval(interval);
  }, [refreshUser]);

  const handleResend = useCallback(async () => {
    setResendState("sending");
    setResendError(null);
    try {
      await resendVerification();
      setResendState("sent");
    } catch (err) {
      setResendState("error");
      setResendError(err instanceof Error ? err.message : "Failed to resend");
    }
  }, []);

  const handleVerify = useCallback(async () => {
    const trimmed = token.trim();
    if (!trimmed) return;
    setVerifyState("verifying");
    setVerifyError(null);
    try {
      await verifyEmail(trimmed);
      await refreshUser();
    } catch (err) {
      setVerifyState("error");
      setVerifyError(err instanceof Error ? err.message : "Verification failed");
    }
  }, [token, refreshUser]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-md space-y-4">
        <div className="text-center mb-6">
          <h1 className="text-2xl font-semibold">Sheaf</h1>
          <p className="text-sm text-muted-foreground mt-1">Almost there</p>
        </div>

        {needsVerification && (
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <Mail className="h-5 w-5 text-muted-foreground" />
                <CardTitle className="text-base">Verify your email</CardTitle>
              </div>
              <CardDescription>
                We sent a verification link and code to <span className="font-medium text-foreground">{user?.email}</span>.
                Click the link, or paste the code below.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                <form
                  onSubmit={(e) => { e.preventDefault(); handleVerify(); }}
                  className="flex gap-2"
                >
                  <Input
                    placeholder="Paste verification code"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    className="font-mono text-sm"
                  />
                  <Button
                    type="submit"
                    size="sm"
                    disabled={!token.trim() || verifyState === "verifying"}
                  >
                    {verifyState === "verifying" ? "Verifying..." : "Verify"}
                  </Button>
                </form>
                {verifyError && (
                  <p className="text-sm text-destructive">{verifyError}</p>
                )}
                <div className="flex items-center gap-2">
                  {resendState === "sent" ? (
                    <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
                      <Check className="h-3.5 w-3.5" />
                      Verification email sent
                    </span>
                  ) : (
                    <Button
                      variant="link"
                      size="sm"
                      className="h-auto p-0 text-xs text-muted-foreground"
                      onClick={handleResend}
                      disabled={resendState === "sending"}
                    >
                      {resendState === "sending" ? "Sending..." : "Resend verification email"}
                    </Button>
                  )}
                  {resendError && (
                    <span className="text-xs text-destructive">{resendError}</span>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {needsApproval && (
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <Clock className="h-5 w-5 text-muted-foreground" />
                <CardTitle className="text-base">Waiting for approval</CardTitle>
              </div>
              <CardDescription>
                Your account is pending admin approval. You'll {needsVerification ? "also " : ""}be able to use Sheaf once an admin reviews your registration.
                {needsVerification && " Please verify your email in the meantime."}
              </CardDescription>
            </CardHeader>
          </Card>
        )}

        <div className="text-center">
          <Button variant="ghost" size="sm" onClick={logout} className="text-muted-foreground">
            <LogOut className="h-4 w-4 mr-2" />
            Log out
          </Button>
        </div>
      </div>
    </div>
  );
}
