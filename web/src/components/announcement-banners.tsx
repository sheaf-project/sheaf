import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  getActiveAnnouncements,
  getLoggedOutAnnouncements,
  type Announcement,
} from "@/lib/announcements";
import { apiFetch } from "@/lib/api-client";
import { patchWebSettings } from "@/lib/client-settings";
import { Button } from "@/components/ui/button";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { severityConfig } from "@/components/banner-severity";

function useClientSettings(clientId: string) {
  return useQuery({
    queryKey: ["client-settings", clientId],
    queryFn: async () => {
      try {
        const res = await apiFetch<{ settings: Record<string, unknown> }>(
          `/v1/settings/client/${encodeURIComponent(clientId)}`,
          // No stored blob for this client 404s by design (e.g. a fresh
          // account with nothing dismissed yet); expected, so don't toast.
          { skipErrorToast: true },
        );
        return res.settings;
      } catch {
        return {};
      }
    },
    staleTime: 5 * 60 * 1000,
  });
}

export function AnnouncementBanners() {
  const qc = useQueryClient();
  const { data: announcements } = useQuery({
    queryKey: ["announcements"],
    queryFn: getActiveAnnouncements,
    staleTime: 60 * 1000,
  });
  const { data: settings } = useClientSettings("web");

  // Session-only dismissals (cleared on page reload)
  const [sessionDismissed, setSessionDismissed] = useState<Set<string>>(
    new Set(),
  );

  const permanentlyDismissed: string[] = Array.isArray(
    settings?.dismissed_announcements,
  )
    ? (settings.dismissed_announcements as string[])
    : [];

  if (!announcements?.length) return null;

  const visible = announcements.filter(
    (a) =>
      !sessionDismissed.has(a.id) && !permanentlyDismissed.includes(a.id),
  );

  if (!visible.length) return null;

  function handleDismiss(id: string) {
    setSessionDismissed((prev) => new Set(prev).add(id));
  }

  async function handleDontShowAgain(id: string) {
    const updated = [...permanentlyDismissed, id];
    try {
      await patchWebSettings({ dismissed_announcements: updated });
      qc.invalidateQueries({ queryKey: ["client-settings", "web"] });
    } catch {
      // Fall back to session dismiss
    }
    setSessionDismissed((prev) => new Set(prev).add(id));
  }

  return (
    <div className="flex flex-col">
      {visible.map((a) => (
        <AnnouncementBanner
          key={a.id}
          announcement={a}
          onDismiss={() => handleDismiss(a.id)}
          onDontShowAgain={() => handleDontShowAgain(a.id)}
        />
      ))}
    </div>
  );
}

export function LoggedOutAnnouncementBanners() {
  const { data: announcements } = useQuery({
    queryKey: ["announcements", "logged-out"],
    queryFn: getLoggedOutAnnouncements,
    staleTime: 60 * 1000,
  });

  const [sessionDismissed, setSessionDismissed] = useState<Set<string>>(
    new Set(),
  );

  if (!announcements?.length) return null;

  const visible = announcements.filter((a) => !sessionDismissed.has(a.id));
  if (!visible.length) return null;

  function handleDismiss(id: string) {
    setSessionDismissed((prev) => new Set(prev).add(id));
  }

  return (
    <div className="flex flex-col">
      {visible.map((a) => (
        <AnnouncementBanner
          key={a.id}
          announcement={a}
          onDismiss={() => handleDismiss(a.id)}
          onDontShowAgain={() => handleDismiss(a.id)}
          showDontShowAgain={false}
        />
      ))}
    </div>
  );
}

// Announcement bodies support inline markdown, mainly so an admin can
// include a link (e.g. to a full incident write-up). This is a one-line
// banner, so only inline elements are allowed - any block markdown
// (headings, images, lists, code blocks) is unwrapped to its plain text so
// it can never break the banner layout. react-markdown sanitises link URLs
// by default (javascript:/data: are dropped); links open in a new tab.
function AnnouncementBody({ body }: { body: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      allowedElements={["p", "a", "em", "strong", "del", "code", "br"]}
      unwrapDisallowed
      components={{
        p: ({ children }) => <>{children}</>,
        a: ({ href, children }) => (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2 hover:opacity-80"
          >
            {children}
          </a>
        ),
      }}
    >
      {body}
    </ReactMarkdown>
  );
}

function AnnouncementBanner({
  announcement,
  onDismiss,
  onDontShowAgain,
  showDontShowAgain = true,
}: {
  announcement: Announcement;
  onDismiss: () => void;
  onDontShowAgain: () => void;
  showDontShowAgain?: boolean;
}) {
  const config = severityConfig[announcement.severity] ?? severityConfig.info;
  const Icon = config.icon;

  return (
    <div
      className={cn(
        "flex items-start gap-3 border-b px-4 py-2 text-sm",
        config.bg,
        config.border,
      )}
    >
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", config.iconColor)} />
      <div className={cn("flex-1 min-w-0", config.text)}>
        <span className="font-semibold">{announcement.title}</span>
        {announcement.body && (
          <>
            <span className="mx-2 opacity-50" aria-hidden="true">
              ·
            </span>
            <span className="opacity-90">
              <AnnouncementBody body={announcement.body} />
            </span>
          </>
        )}
      </div>
      {announcement.dismissible && (
        <div className="flex items-center gap-1 shrink-0">
          {showDontShowAgain && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs opacity-70 hover:opacity-100"
              onClick={onDontShowAgain}
            >
              Don't show again
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 opacity-70 hover:opacity-100"
            onClick={onDismiss}
            aria-label="Dismiss"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}
    </div>
  );
}
