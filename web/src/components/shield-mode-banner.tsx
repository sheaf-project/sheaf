import { useQuery } from "@tanstack/react-query";

import { severityConfig } from "@/components/banner-severity";
import { getShieldModeStatus } from "@/lib/shield-mode";
import { cn } from "@/lib/utils";

/** Top-of-app banner shown while the operator has cf-shield engaged.
 *
 *  Only logged-in users with their session still alive ever see this:
 *  by definition, anyone with `disable_cdn_during_ddos=true` has just
 *  been bounced to login when the operator flipped state, so the
 *  audience for the banner is the people for whom the CDN is doing
 *  its job. The banner is purely informational - it tells them why
 *  responses might feel a bit different (challenge interstitials,
 *  slower TLS handshakes through the CF edge) and that things are
 *  expected to return to normal once mitigation lifts.
 *
 *  Renders null when feature is dormant or shield is inactive, so it
 *  is safe to drop into the layout unconditionally. */
export function ShieldModeBanner() {
  const { data } = useQuery({
    queryKey: ["shield-mode-status"],
    queryFn: getShieldModeStatus,
    // Poll while a session is live so the banner clears within a
    // minute of the operator running `cf-shield down`. Backend cost is
    // a single Redis GET per call.
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });

  if (!data?.feature_enabled || !data.active) return null;

  const config = severityConfig.warning;
  const Icon = config.icon;
  return (
    <div
      className={cn(
        "flex items-center gap-3 border-b px-4 py-2 text-sm",
        config.bg,
        config.border,
      )}
    >
      <Icon className={cn("h-4 w-4 shrink-0", config.iconColor)} />
      <span className={cn("flex-1 min-w-0", config.text)}>
        DDoS mitigation is active. Traffic is currently routed via
        Cloudflare. Sheaf is otherwise operating normally.
      </span>
    </div>
  );
}
