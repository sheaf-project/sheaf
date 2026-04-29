import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import {
  type BannerSeverity,
  severityConfig,
} from "@/components/banner-severity";
import { getRetention } from "@/lib/retention";
import { cn } from "@/lib/utils";

const URGENT_WINDOW_MS = 24 * 3_600_000;

function severityFor(effectiveAt: string): BannerSeverity {
  const ms = new Date(effectiveAt).getTime() - Date.now();
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

export function RetentionTrimNoticeBanner() {
  const { data } = useQuery({
    queryKey: ["retention"],
    queryFn: getRetention,
    staleTime: 30 * 1000,
    retry: false,
  });

  const notice = data?.trim_notice;
  if (!notice) return null;

  const severity = severityFor(notice.effective_at);
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
      <span className={cn("flex-1 min-w-0", config.text)}>
        Revision retention trim from {notice.from_tier} → {notice.to_tier} runs{" "}
        {timeRemaining(notice.effective_at)}.
      </span>
      <Link
        to="/settings/safety"
        className={cn("shrink-0 underline text-xs", config.text)}
      >
        Review
      </Link>
    </div>
  );
}
