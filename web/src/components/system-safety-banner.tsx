import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { cn } from "@/lib/utils";
import { getSystemSafety } from "@/lib/system-safety";
import {
  severityConfig,
  type BannerSeverity,
} from "@/components/banner-severity";

const URGENT_WINDOW_MS = 24 * 3_600_000;

function severityFor(finalizeAfter: string): BannerSeverity {
  const ms = new Date(finalizeAfter).getTime() - Date.now();
  return ms < URGENT_WINDOW_MS ? "critical" : "warning";
}

function timeRemaining(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "any moment";
  const hours = Math.ceil(ms / 3_600_000);
  if (hours < 24) return `in ${hours}h`;
  const days = Math.ceil(ms / 86_400_000);
  return `in ${days} day${days === 1 ? "" : "s"}`;
}

export function SystemSafetyBanner() {
  const { data } = useQuery({
    queryKey: ["system-safety"],
    queryFn: getSystemSafety,
    staleTime: 30 * 1000,
    retry: false,
  });

  const actions = data?.pending_actions ?? [];
  const changes = data?.pending_changes ?? [];

  if (actions.length === 0 && changes.length === 0) return null;

  return (
    <div className="flex flex-col">
      {actions.length > 0 && (
        <Banner
          severity={earliestSeverity(actions.map((a) => a.finalize_after))}
          message={
            actions.length === 1
              ? `1 pending destructive action finalizes ${timeRemaining(actions[0].finalize_after)}.`
              : `${actions.length} pending destructive actions — next finalizes ${timeRemaining(earliest(actions.map((a) => a.finalize_after)))}.`
          }
        />
      )}
      {changes.length > 0 && (
        <Banner
          severity={earliestSeverity(changes.map((c) => c.finalize_after))}
          message={
            changes.length === 1
              ? `Safety settings change pending — finalizes ${timeRemaining(changes[0].finalize_after)}.`
              : `${changes.length} safety settings changes pending — next finalizes ${timeRemaining(earliest(changes.map((c) => c.finalize_after)))}.`
          }
        />
      )}
    </div>
  );
}

function earliest(isos: string[]): string {
  return isos.reduce((a, b) =>
    new Date(a).getTime() <= new Date(b).getTime() ? a : b,
  );
}

function earliestSeverity(isos: string[]): BannerSeverity {
  return severityFor(earliest(isos));
}

function Banner({
  severity,
  message,
}: {
  severity: BannerSeverity;
  message: string;
}) {
  const config = severityConfig[severity];
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
      <span className={cn("flex-1 min-w-0", config.text)}>{message}</span>
      <Link
        to="/settings/safety"
        className={cn("shrink-0 underline text-xs", config.text)}
      >
        Review
      </Link>
    </div>
  );
}
