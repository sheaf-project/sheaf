import { type FormEvent, useEffect, useState } from "react";
import { useSearchParams, Link } from "react-router";
import { useTheme } from "@/hooks/use-theme";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PasswordField } from "@/components/password-field";
import { ApiError } from "@/lib/api-client";
import { resetPassword } from "@/lib/auth";
import { Check, X, Sun, Moon } from "lucide-react";

export function ResetPasswordPage() {
  const { theme, toggleTheme } = useTheme();
  const [params] = useSearchParams();
  const urlToken = params.get("token");
  const [manualToken, setManualToken] = useState("");
  const [password, setPassword] = useState("");
  const [state, setState] = useState<"form" | "success" | "error">("form");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const effectiveToken = urlToken || manualToken.trim();

  // Strip the token from the address bar so it doesn't linger in browser
  // history or leak through the Referer header. urlToken is already
  // captured above, so the form still submits with it.
  useEffect(() => {
    if (urlToken) {
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, [urlToken]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!effectiveToken) {
      setError("Please enter a reset token");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      await resetPassword(effectiveToken, password);
      setState("success");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
        setState("error");
      } else {
        setError("Something went wrong");
        setState("error");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Button
        variant="ghost"
        size="icon"
        className="absolute top-4 right-4 text-muted-foreground"
        onClick={toggleTheme}
        aria-label="Toggle theme"
      >
        {theme === "dark" ? (
          <Sun className="h-4 w-4" />
        ) : (
          <Moon className="h-4 w-4" />
        )}
      </Button>
      <Card className="w-full max-w-sm">
        {state === "form" && (
          <>
            <CardHeader className="text-center">
              <CardTitle className="text-2xl font-semibold">Sheaf</CardTitle>
              <CardDescription>Choose a new password</CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSubmit} className="space-y-4">
                {!urlToken && (
                  <div className="space-y-2">
                    <Label htmlFor="reset-token">Reset token</Label>
                    <Input
                      id="reset-token"
                      value={manualToken}
                      onChange={(e) => setManualToken(e.target.value)}
                      placeholder="Paste token from email"
                    />
                    <p className="text-xs text-muted-foreground">
                      Paste the token from your password reset email, or open
                      the link directly.
                    </p>
                  </div>
                )}
                <PasswordField
                  id="new-password"
                  value={password}
                  onChange={setPassword}
                />
                {error && (
                  <p className="text-sm text-destructive-foreground">
                    {error}
                  </p>
                )}
                <Button
                  type="submit"
                  className="w-full"
                  disabled={submitting}
                >
                  {submitting ? "Resetting..." : "Reset password"}
                </Button>
              </form>
            </CardContent>
          </>
        )}
        {state === "success" && (
          <>
            <CardHeader className="text-center">
              <div className="flex justify-center mb-2">
                <Check className="h-8 w-8 text-green-500" />
              </div>
              <CardTitle>Password reset</CardTitle>
              <CardDescription>
                Your password has been changed. You can now log in.
              </CardDescription>
            </CardHeader>
            <CardContent className="text-center">
              <Button asChild>
                <Link to="/login">Go to login</Link>
              </Button>
            </CardContent>
          </>
        )}
        {state === "error" && (
          <>
            <CardHeader className="text-center">
              <div className="flex justify-center mb-2">
                <X className="h-8 w-8 text-destructive" />
              </div>
              <CardTitle>Reset failed</CardTitle>
              <CardDescription>{error}</CardDescription>
            </CardHeader>
            <CardContent className="text-center space-y-2">
              <Button asChild variant="outline">
                <Link to="/forgot-password">Request a new link</Link>
              </Button>
            </CardContent>
          </>
        )}
      </Card>
    </div>
  );
}
