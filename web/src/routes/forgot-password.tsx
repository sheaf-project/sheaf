import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ThemeModeToggle } from "@/components/theme-mode-toggle";
import { ApiError } from "@/lib/api-client";
import { requestPasswordReset } from "@/lib/auth";

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await requestPasswordReset(email);
      setSubmitted(true);
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

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <ThemeModeToggle className="absolute top-4 right-4 text-muted-foreground" />
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl font-semibold">Sheaf</CardTitle>
          <p className="text-sm text-muted-foreground">Reset your password</p>
        </CardHeader>
        <CardContent>
          {submitted ? (
            <div className="space-y-4 text-center">
              <p className="text-sm">
                If an account with that email exists, we've sent a password
                reset link. Check your inbox.
              </p>
              <Link
                to="/login"
                className="text-sm text-primary hover:underline"
              >
                Back to login
              </Link>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="reset-email">Email</Label>
                <Input
                  id="reset-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoFocus
                />
              </div>
              {error && (
                <p className="text-sm text-destructive-foreground">{error}</p>
              )}
              <Button type="submit" className="w-full" disabled={submitting}>
                {submitting ? "Sending..." : "Send reset link"}
              </Button>
              <div className="text-center">
                <Link
                  to="/login"
                  className="text-sm text-muted-foreground hover:underline"
                >
                  Back to login
                </Link>
              </div>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
