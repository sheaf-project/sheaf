import { useEffect, useRef, useState } from "react";
import { useSearchParams, Link } from "react-router";
import { apiFetch } from "@/lib/api-client";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Check, X } from "lucide-react";

export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get("token");
  const [state, setState] = useState<"loading" | "success" | "error">(token ? "loading" : "error");
  const [error, setError] = useState<string | null>(token ? null : "Missing verification token");
  const calledRef = useRef(false);

  useEffect(() => {
    if (!token || calledRef.current) return;
    calledRef.current = true;

    apiFetch<{ verified: boolean }>(`/v1/auth/verify-email?token=${encodeURIComponent(token)}`)
      .then(() => setState("success"))
      .catch((err) => {
        setState("error");
        setError(err instanceof Error ? err.message : "Verification failed");
      });
  }, [token]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm">
        {state === "loading" && (
          <CardHeader className="text-center">
            <CardTitle>Verifying...</CardTitle>
          </CardHeader>
        )}
        {state === "success" && (
          <>
            <CardHeader className="text-center">
              <div className="flex justify-center mb-2">
                <Check className="h-8 w-8 text-green-500" />
              </div>
              <CardTitle>Email verified</CardTitle>
              <CardDescription>
                Your email has been verified. You can now use Sheaf.
              </CardDescription>
            </CardHeader>
            <CardContent className="text-center">
              <Button asChild>
                <Link to="/">Continue to Sheaf</Link>
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
              <CardTitle>Verification failed</CardTitle>
              <CardDescription>
                {error}
              </CardDescription>
            </CardHeader>
            <CardContent className="text-center">
              <Button asChild variant="outline">
                <Link to="/">Back to Sheaf</Link>
              </Button>
            </CardContent>
          </>
        )}
      </Card>
    </div>
  );
}
