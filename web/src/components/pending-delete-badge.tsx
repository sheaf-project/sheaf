import { Link } from "react-router";
import { Clock } from "lucide-react";

import { useDateFormatters } from "@/hooks/use-date-formatters";
import { cn } from "@/lib/utils";

/** Compact "Pending delete - finalises in Nd" badge for list items whose
 *  System Safety pending action is still in the grace window. Renders null
 *  when the timestamp is null so it's safe to drop unconditionally into a
 *  list row. Clicking deep-links to the Safety settings where the user can
 *  cancel the queued action. */
export function PendingDeleteBadge({
  finalizeAt,
  className,
}: {
  finalizeAt: string | null | undefined;
  className?: string;
}) {
  const { formatDate, formatDateTime } = useDateFormatters();
  if (!finalizeAt) return null;
  // Show the absolute finalize date - pure across renders (no Date.now in
  // the render path) and more informative anyway for a grace window of
  // days. The Settings -> Safety page shows the exact countdown.
  const label = formatDate(finalizeAt);
  // Deliberately NOT routed through `ReasonBadge` like the sharing badges: this
  // one is a navigation link, and wrapping it in a popover trigger would have
  // it announce itself as opening a dialog when it actually goes to a page. The
  // half of the gap that applies here - the reason being invisible to a screen
  // reader - is closed by naming the link with the same sentence the title
  // carries, which needs no trigger at all.
  const reason = `Pending delete - finalises ${formatDateTime(finalizeAt)}. Click to cancel.`;
  return (
    <Link
      to="/settings/safety"
      title={reason}
      aria-label={reason}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border border-amber-500/50 bg-amber-500/10 px-2 py-0.5 text-xs text-amber-700 hover:bg-amber-500/20 dark:text-amber-400",
        className,
      )}
    >
      <Clock className="h-3 w-3" />
      Pending delete · {label}
    </Link>
  );
}

