import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { X } from "lucide-react";
import {
  describeAdminAction,
  getMyAdminActivity,
  isCriticalAdminAction,
  type UserAdminActivityEvent,
} from "@/lib/admin";
import { patchWebSettings } from "@/lib/client-settings";
import { useClientSettings } from "@/hooks/use-client-settings";
import { severityConfig } from "@/components/banner-severity";
import { Button } from "@/components/ui/button";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { cn } from "@/lib/utils";

/** Tells the account's owner when an administrator has acted on their
 *  account, until they say they have seen it.
 *
 *  Driven by the same self-only admin-activity feed the Account settings
 *  card shows, so nothing an admin does can reach the card without also
 *  reaching here. "Seen" is a timestamp in the web client-settings blob,
 *  the way announcement dismissals persist: it follows the account across
 *  browsers rather than living in one tab, and a dismissal can never hide
 *  an action that happens after it. Only the first page is read; more than
 *  a page of unseen admin actions is not a banner problem. */
export function AdminActionBanner() {
  const qc = useQueryClient();
  const { formatDateTime } = useDateFormatters();
  const { data: events } = useQuery({
    queryKey: ["admin-activity", "me"],
    queryFn: () => getMyAdminActivity(1, 20),
    staleTime: 60 * 1000,
    retry: false,
  });
  const { data: settings } = useClientSettings("web");
  const [sessionSeenUpTo, setSessionSeenUpTo] = useState<string | null>(null);

  const seenUpTo =
    sessionSeenUpTo ??
    (typeof settings?.admin_activity_seen_at === "string"
      ? settings.admin_activity_seen_at
      : null);
  // Events arrive newest first; a lexical compare on ISO-8601 is a time
  // compare, which keeps this independent of the browser's Date parsing.
  const unseen: UserAdminActivityEvent[] = (events ?? []).filter(
    (e) => seenUpTo === null || e.created_at > seenUpTo,
  );
  if (unseen.length === 0) return null;

  const newest = unseen[0];
  const severity = unseen.some((e) => isCriticalAdminAction(e.action))
    ? "critical"
    : "warning";
  const config = severityConfig[severity];
  const Icon = config.icon;

  async function dismiss() {
    const upTo = newest.created_at;
    setSessionSeenUpTo(upTo);
    try {
      await patchWebSettings({ admin_activity_seen_at: upTo });
      qc.invalidateQueries({ queryKey: ["client-settings", "web"] });
    } catch {
      // The session-only dismissal above still applies; it comes back on
      // the next load, which is the right failure mode for this banner.
    }
  }

  return (
    <div
      className={cn(
        "flex items-start gap-3 border-b px-4 py-2 text-sm",
        config.bg,
        config.border,
      )}
      role="status"
    >
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", config.iconColor)} />
      <div className={cn("flex-1 min-w-0", config.text)}>
        <span className="font-semibold">
          An administrator {describeAdminAction(newest.action)}
        </span>
        <span className="opacity-80"> on {formatDateTime(newest.created_at)}.</span>
        {newest.reason && (
          <span className="opacity-90"> Reason given: {newest.reason}.</span>
        )}
        {unseen.length > 1 && (
          <span className="opacity-80">
            {" "}
            Plus {unseen.length - 1} more {unseen.length === 2 ? "action" : "actions"}.
          </span>
        )}{" "}
        <Link to="/settings/account" className="underline underline-offset-2">
          Details
        </Link>
      </div>
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6 shrink-0 opacity-70 hover:opacity-100"
        onClick={dismiss}
        aria-label="Dismiss"
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
