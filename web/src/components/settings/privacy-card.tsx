import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";
import { updateMe } from "@/lib/auth";
import { getShieldModeStatus } from "@/lib/shield-mode";

/** Privacy/security toggles that don't fit the Account card. Currently
 *  one knob: refuse Cloudflare proxying during cf-shield mitigation
 *  (sign-out, not bypass - origin is closed during the event so the
 *  user cannot reach Sheaf at all until mitigation lifts). The card
 *  only renders
 *  when the instance reports `feature_enabled` on /v1/shield-mode/status
 *  - selfhosters without a Cloudflare break-glass setup never see it.
 *
 *  When more privacy preferences land (e.g. analytics opt-out, future
 *  client-side telemetry toggles), they slot in here. */
export function PrivacyCard() {
  const { user, refreshUser } = useAuth();
  const { data: shieldStatus } = useQuery({
    queryKey: ["shield-mode-status"],
    queryFn: getShieldModeStatus,
    // Refetch on focus is enough; this is a low-traffic status read.
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  });

  const cdnToggle = useMutation({
    mutationFn: (disable_cdn_during_ddos: boolean) =>
      updateMe({ disable_cdn_during_ddos }),
    onSuccess: async () => {
      await refreshUser();
      toast.success("Preference saved");
    },
    onError: () => toast.error("Failed to save preference"),
  });

  if (!shieldStatus?.feature_enabled) {
    // Dormant feature - don't render the card at all rather than show
    // a toggle that does nothing visible to the user.
    return null;
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Privacy</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="flex items-start gap-3">
          <Checkbox
            id="disable-cdn-during-ddos"
            checked={user?.disable_cdn_during_ddos ?? false}
            onCheckedChange={(v) => cdnToggle.mutate(v === true)}
            disabled={cdnToggle.isPending}
          />
          <div>
            <Label
              htmlFor="disable-cdn-during-ddos"
              className="text-sm font-medium cursor-pointer"
            >
              Refuse Cloudflare proxying (signs you out during
              mitigation)
            </Label>
            <p className="text-xs text-muted-foreground mt-0.5">
              When the operator engages DDoS mitigation, traffic is
              temporarily routed through Cloudflare and the direct
              origin is closed. Enabling this means your sessions are
              ended the moment mitigation is engaged, and you cannot
              sign back in until mitigation clears. Sheaf is
              effectively unreachable to you for the duration. Choose
              this if you would rather wait out the incident than have
              your traffic proxied through Cloudflare. Has no effect
              outside an active mitigation window.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
