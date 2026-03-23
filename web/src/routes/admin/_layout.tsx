import { useState } from "react";
import { Navigate, Outlet } from "react-router";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useAuth } from "@/hooks/use-auth";
import { getAdminAuthStatus, verifyAdminStepUp } from "@/lib/admin";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function StepUpForm({ level, totpEnabled }: { level: "password" | "totp"; totpEnabled: boolean }) {
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const verify = useMutation({
    mutationFn: verifyAdminStepUp,
    onSuccess: () => setVerified(true),
    onError: (e: Error) => setError(e.message),
  });

  if (verified) return <Outlet />;

  if (level === "totp" && !totpEnabled) {
    return (
      <div className="flex items-center justify-center min-h-[40vh]">
        <Card className="w-full max-w-sm">
          <CardHeader>
            <CardTitle>TOTP required</CardTitle>
            <CardDescription>
              The server requires TOTP authentication to access the admin dashboard.
              Enable TOTP in Settings before continuing.
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    verify.mutate(level === "password" ? { password } : { totp_code: totpCode });
  };

  return (
    <div className="flex items-center justify-center min-h-[40vh]">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Confirm your identity</CardTitle>
          <CardDescription>
            {level === "password"
              ? "Enter your password to access the admin dashboard."
              : "Enter your authenticator code to access the admin dashboard."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {level === "password" ? (
              <div className="space-y-2">
                <Label htmlFor="admin-password">Password</Label>
                <Input
                  id="admin-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoFocus
                  required
                />
              </div>
            ) : (
              <div className="space-y-2">
                <Label htmlFor="admin-totp">Authenticator code</Label>
                <Input
                  id="admin-totp"
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  placeholder="000000"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                  autoFocus
                  required
                />
              </div>
            )}
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={verify.isPending}>
              {verify.isPending ? "Verifying…" : "Continue"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

export function AdminLayout() {
  const { user, loading } = useAuth();

  const { data: authStatus, isLoading: authLoading } = useQuery({
    queryKey: ["admin", "auth"],
    queryFn: getAdminAuthStatus,
    enabled: !!user?.is_admin,
    retry: false,
  });

  if (loading || (user?.is_admin && authLoading)) return null;
  if (!user?.is_admin) return <Navigate to="/" replace />;

  // If the status check fails (e.g. network error), fall through — API
  // endpoints are still protected server-side regardless.
  if (!authStatus) return <Outlet />;

  if (!authStatus.verified && authStatus.level !== "none") {
    return (
      <StepUpForm
        level={authStatus.level as "password" | "totp"}
        totpEnabled={authStatus.totp_enabled}
      />
    );
  }

  return <Outlet />;
}
