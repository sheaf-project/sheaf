import { type FormEvent, useState } from "react";
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
import { PasswordField } from "@/components/password-field";
import { ApiError } from "@/lib/api-client";
import { resetPassword } from "@/lib/auth";
import { Check, X, Sun, Moon } from "lucide-react";

export function ResetPasswordPage() {
  const { theme, toggleTheme } = useTheme();
  const [params] = useSearchParams();
  const token = params.get("token");
  const [password, setPassword] = useState("");
  const [state, setState] = useState<"form" | "success" | "error">(
    token ? "form" : "error",
  );
  const [error, setError] = useState(token ? "" : "Missing reset token");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setError("");
    setSubmitting(true);
    try {
      await resetPassword(token, password);
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
