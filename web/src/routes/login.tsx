import { type FormEvent, useState } from "react";
import { Navigate } from "react-router";
import { useAuth } from "@/hooks/use-auth";
import { useTheme } from "@/hooks/use-theme";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PasswordField } from "@/components/password-field";
import { ApiError } from "@/lib/api-client";
import { Sun, Moon } from "lucide-react";

export function LoginPage() {
  const { user, loading, login, register } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [needs2FA, setNeeds2FA] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (loading) return null;
  if (user) return <Navigate to="/" replace />;

  async function handleSubmit(action: "login" | "register", e: FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      if (action === "login") {
        await login(email, password, totpCode || undefined);
      } else {
        await register(email, password);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.detail === "TOTP code required" && !needs2FA) {
          setNeeds2FA(true);
        } else {
          setError(err.detail);
        }
      } else {
        setError("Something went wrong");
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
        <CardHeader className="text-center">
          <CardTitle className="text-2xl font-semibold">Sheaf</CardTitle>
          <p className="text-sm text-muted-foreground">
            Plural system tracking
          </p>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="login">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="login">Login</TabsTrigger>
              <TabsTrigger value="register">Register</TabsTrigger>
            </TabsList>
            <TabsContent value="login">
              <form
                onSubmit={(e) => handleSubmit("login", e)}
                className="space-y-4 pt-4"
              >
                <div className="space-y-2">
                  <Label htmlFor="login-email">Email</Label>
                  <Input
                    id="login-email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="login-password">Password</Label>
                  <Input
                    id="login-password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                  />
                </div>
                {needs2FA && (
                  <div className="space-y-2">
                    <Label htmlFor="login-totp">2FA code</Label>
                    <Input
                      id="login-totp"
                      value={totpCode}
                      onChange={(e) => setTotpCode(e.target.value)}
                      placeholder="6-digit code or recovery code"
                      autoFocus
                    />
                  </div>
                )}
                {error && (
                  <p className="text-sm text-destructive-foreground">{error}</p>
                )}
                <Button type="submit" className="w-full" disabled={submitting}>
                  {submitting ? "Signing in..." : "Sign in"}
                </Button>
              </form>
            </TabsContent>
            <TabsContent value="register">
              <form
                onSubmit={(e) => handleSubmit("register", e)}
                className="space-y-4 pt-4"
              >
                <div className="space-y-2">
                  <Label htmlFor="reg-email">Email</Label>
                  <Input
                    id="reg-email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                  />
                </div>
                <PasswordField
                  id="reg-password"
                  value={password}
                  onChange={setPassword}
                />
                {error && (
                  <p className="text-sm text-destructive-foreground">{error}</p>
                )}
                <Button type="submit" className="w-full" disabled={submitting}>
                  {submitting ? "Creating account..." : "Create account"}
                </Button>
              </form>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}
