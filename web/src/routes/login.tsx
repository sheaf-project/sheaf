import { type FormEvent, useEffect, useState } from "react";
import { Link, Navigate } from "react-router";
import { useAuth } from "@/hooks/use-auth";
import { useTheme } from "@/hooks/use-theme";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Checkbox } from "@/components/ui/checkbox";
import { PasswordField } from "@/components/password-field";
import { Captcha } from "@/components/captcha";
import { Logo } from "@/components/logo";
import { LegalFooter } from "@/components/legal-footer";
import { LoggedOutAnnouncementBanners } from "@/components/announcement-banners";
import { ApiError } from "@/lib/api-client";
import { type AuthConfig, getAuthConfig } from "@/lib/auth";
import { Sun, Moon } from "lucide-react";

export function LoginPage() {
  const { user, loading, login, register } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [newsletterOptIn, setNewsletterOptIn] = useState(false);
  const [termsAgreed, setTermsAgreed] = useState(false);
  const [totpCode, setTotpCode] = useState("");
  const [needs2FA, setNeeds2FA] = useState(false);
  const [rememberDevice, setRememberDevice] = useState(false);
  const [deviceNickname, setDeviceNickname] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [registerCaptcha, setRegisterCaptcha] = useState("");
  const [loginCaptcha, setLoginCaptcha] = useState("");

  useEffect(() => {
    getAuthConfig().then(setConfig).catch(() => {});
  }, []);

  if (loading) return null;
  if (user) return <Navigate to="/" replace />;

  const registrationClosed = config?.registration_mode === "closed";
  const showInviteField =
    config?.registration_mode === "invite" || config?.invite_codes_enabled;
  const captchaOnRegister = !!config?.captcha_provider;
  const captchaOnLogin = !!config?.captcha_provider && config.captcha_on_login;

  async function handleSubmit(action: "login" | "register", e: FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      if (action === "login") {
        await login(
          email,
          password,
          totpCode || undefined,
          loginCaptcha || undefined,
          rememberDevice,
          rememberDevice ? deviceNickname.trim() || undefined : undefined,
        );
      } else {
        await register(
          email,
          password,
          inviteCode || undefined,
          newsletterOptIn,
          registerCaptcha || undefined,
        );
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
    <div className="relative flex min-h-screen flex-col bg-background">
      <LoggedOutAnnouncementBanners />
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
      <div className="flex flex-1 items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <Logo className="mx-auto mb-2 h-16 w-16 rounded-2xl" />
          <CardTitle className="text-2xl font-semibold">Sheaf</CardTitle>
          <p className="text-sm text-muted-foreground">
            Plural system tracking
          </p>
        </CardHeader>
        <CardContent>
          {registrationClosed ? (
            // No register tab when registration is closed
            <form
              onSubmit={(e) => handleSubmit("login", e)}
              className="space-y-4"
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
                <>
                  <div className="space-y-2">
                    <Label htmlFor="login-totp">2FA code</Label>
                    <Input
                      id="login-totp"
                      value={totpCode}
                      onChange={(e) => setTotpCode(e.target.value)}
                      placeholder="6-digit code or recovery code"
                      autoComplete="off"
                      autoFocus
                    />
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-start gap-3">
                      <Checkbox
                        id="login-remember"
                        checked={rememberDevice}
                        onCheckedChange={(v) => setRememberDevice(v === true)}
                      />
                      <Label
                        htmlFor="login-remember"
                        className="text-sm font-normal leading-snug cursor-pointer"
                      >
                        Trust this device for 30 days. Skip the 2FA prompt on
                        this browser until then.
                      </Label>
                    </div>
                    {rememberDevice && (
                      <Input
                        id="login-device-nickname"
                        value={deviceNickname}
                        onChange={(e) => setDeviceNickname(e.target.value)}
                        placeholder="Device name (optional)"
                        maxLength={128}
                      />
                    )}
                  </div>
                </>
              )}
              {captchaOnLogin && (
                <Captcha
                  onVerified={setLoginCaptcha}
                  onReset={() => setLoginCaptcha("")}
                />
              )}
              {error && (
                <p className="text-sm text-destructive-foreground">{error}</p>
              )}
              <Button
                type="submit"
                className="w-full"
                disabled={submitting || (captchaOnLogin && !loginCaptcha)}
              >
                {submitting ? "Signing in..." : "Sign in"}
              </Button>
              {config?.email_enabled && (
                <div className="text-center">
                  <Link
                    to="/forgot-password"
                    className="text-sm text-muted-foreground hover:underline"
                  >
                    Forgot password?
                  </Link>
                </div>
              )}
            </form>
          ) : (
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
                    <>
                      <div className="space-y-2">
                        <Label htmlFor="login-totp">2FA code</Label>
                        <Input
                          id="login-totp"
                          value={totpCode}
                          onChange={(e) => setTotpCode(e.target.value)}
                          placeholder="6-digit code or recovery code"
                          autoComplete="off"
                          autoFocus
                        />
                      </div>
                      <div className="flex items-start gap-3">
                        <Checkbox
                          id="login-remember"
                          checked={rememberDevice}
                          onCheckedChange={(v) => setRememberDevice(v === true)}
                        />
                        <Label
                          htmlFor="login-remember"
                          className="text-sm font-normal leading-snug cursor-pointer"
                        >
                          Trust this device for 30 days. Skip the 2FA prompt on
                          this browser until then.
                        </Label>
                      </div>
                    </>
                  )}
                  {captchaOnLogin && (
                    <Captcha
                      onVerified={setLoginCaptcha}
                      onReset={() => setLoginCaptcha("")}
                    />
                  )}
                  {error && (
                    <p className="text-sm text-destructive-foreground">
                      {error}
                    </p>
                  )}
                  <Button
                    type="submit"
                    className="w-full"
                    disabled={submitting || (captchaOnLogin && !loginCaptcha)}
                  >
                    {submitting ? "Signing in..." : "Sign in"}
                  </Button>
                  {config?.email_enabled && (
                    <div className="text-center">
                      <Link
                        to="/forgot-password"
                        className="text-sm text-muted-foreground hover:underline"
                      >
                        Forgot password?
                      </Link>
                    </div>
                  )}
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
                  {showInviteField && (
                    <div className="space-y-2">
                      <Label htmlFor="reg-invite">
                        Invite code
                        {config?.registration_mode !== "invite" && (
                          <span className="text-muted-foreground font-normal">
                            {" "}
                            (optional)
                          </span>
                        )}
                      </Label>
                      <Input
                        id="reg-invite"
                        value={inviteCode}
                        onChange={(e) => setInviteCode(e.target.value)}
                        required={config?.registration_mode === "invite"}
                      />
                    </div>
                  )}
                  <div className="flex items-start gap-3 pt-1">
                    <Checkbox
                      id="reg-newsletter"
                      checked={newsletterOptIn}
                      onCheckedChange={(v) => setNewsletterOptIn(v === true)}
                    />
                    <Label
                      htmlFor="reg-newsletter"
                      className="text-sm font-normal leading-snug cursor-pointer"
                    >
                      Email me occasional updates about Sheaf (new features,
                      important changes). Unchecked by default — you can change
                      this anytime in settings.
                    </Label>
                  </div>
                  {config?.terms_url && (
                    <div className="flex items-start gap-3">
                      <Checkbox
                        id="reg-terms"
                        checked={termsAgreed}
                        onCheckedChange={(v) => setTermsAgreed(v === true)}
                      />
                      <Label
                        htmlFor="reg-terms"
                        className="text-sm font-normal leading-snug cursor-pointer"
                      >
                        I agree to the{" "}
                        <a
                          href={config.terms_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="underline"
                        >
                          Terms of Service
                        </a>
                        {config.privacy_url && (
                          <>
                            {" "}and{" "}
                            <a
                              href={config.privacy_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="underline"
                            >
                              Privacy Policy
                            </a>
                          </>
                        )}
                        .
                      </Label>
                    </div>
                  )}
                  {captchaOnRegister && (
                    <Captcha
                      onVerified={setRegisterCaptcha}
                      onReset={() => setRegisterCaptcha("")}
                    />
                  )}
                  {error && (
                    <p className="text-sm text-destructive-foreground">
                      {error}
                    </p>
                  )}
                  <Button
                    type="submit"
                    className="w-full"
                    disabled={
                      submitting
                      || (!!config?.terms_url && !termsAgreed)
                      || (captchaOnRegister && !registerCaptcha)
                    }
                  >
                    {submitting ? "Creating account..." : "Create account"}
                  </Button>
                </form>
              </TabsContent>
            </Tabs>
          )}
        </CardContent>
      </Card>
      </div>
      <LegalFooter />
    </div>
  );
}
