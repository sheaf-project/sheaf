import { Link } from "react-router";
import { Clock } from "lucide-react";

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
  if (!finalizeAt) return null;
  // Show the absolute finalize date - pure across renders (no Date.now in
  // the render path) and more informative anyway for a grace window of
  // days. The Settings -> Safety page shows the exact countdown.
  const date = new Date(finalizeAt);
  const label = date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
  return (
    <Link
      to="/settings/safety"
      title={`Pending delete - finalises ${date.toLocaleString()}. Click to cancel.`}
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

