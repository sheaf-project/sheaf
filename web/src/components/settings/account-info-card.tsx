import { useMutation } from "@tanstack/react-query";
import { useAuth } from "@/hooks/use-auth";
import { updateMe } from "@/lib/auth";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { TOTPSetup } from "@/components/totp-setup";
import { ChangePassword } from "@/components/change-password";
import { ChangeEmail } from "@/components/change-email";
import { toast } from "sonner";

export function AccountInfoCard() {
  const { user, refreshUser } = useAuth();
  const newsletterToggle = useMutation({
    mutationFn: (newsletter_opt_in: boolean) => updateMe({ newsletter_opt_in }),
    onSuccess: async () => {
      await refreshUser();
      toast.success("Preferences saved");
    },
    onError: () => toast.error("Failed to save preferences"),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Account</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="space-y-2">
          <div>
            <span className="text-muted-foreground">Email:</span> {user?.email}
          </div>
          <div>
            <span className="text-muted-foreground">Tier:</span>{" "}
            <Badge variant="outline">{user?.tier}</Badge>
          </div>
        </div>
        <Separator />
        <div className="space-y-2">
          <p className="text-sm font-medium">Email</p>
          <ChangeEmail />
        </div>
        <Separator />
        <div className="space-y-2">
          <p className="text-sm font-medium">Password</p>
          <ChangePassword />
        </div>
        <Separator />
        <div className="space-y-2">
          <p className="text-sm font-medium">Two-factor authentication</p>
          <TOTPSetup />
        </div>
        <Separator />
        <div className="flex items-start gap-3">
          <Checkbox
            id="newsletter-opt-in"
            checked={user?.newsletter_opt_in ?? false}
            onCheckedChange={(v) => newsletterToggle.mutate(v === true)}
            disabled={newsletterToggle.isPending}
          />
          <div>
            <Label
              htmlFor="newsletter-opt-in"
              className="text-sm font-medium cursor-pointer"
            >
              Product updates email
            </Label>
            <p className="text-xs text-muted-foreground mt-0.5">
              Occasional updates about Sheaf — new features and important
              changes. Transactional mail (password reset, security alerts,
              etc.) is not affected by this setting.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
