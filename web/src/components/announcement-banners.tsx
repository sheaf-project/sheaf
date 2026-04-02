import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getActiveAnnouncements, type Announcement } from "@/lib/announcements";
import { apiFetch } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Info, AlertTriangle, AlertOctagon, X } from "lucide-react";
import { cn } from "@/lib/utils";

const severityConfig = {
  info: {
    icon: Info,
    bg: "bg-blue-500/10",
    border: "border-blue-500/20",
    text: "text-blue-700 dark:text-blue-300",
    iconColor: "text-blue-500",
  },
  warning: {
    icon: AlertTriangle,
    bg: "bg-yellow-500/10",
    border: "border-yellow-500/20",
    text: "text-yellow-800 dark:text-yellow-200",
    iconColor: "text-yellow-500",
  },
  critical: {
    icon: AlertOctagon,
    bg: "bg-destructive/10",
    border: "border-destructive/20",
    text: "text-destructive",
    iconColor: "text-destructive",
  },
};

function useClientSettings(clientId: string) {
  return useQuery({
    queryKey: ["client-settings", clientId],
    queryFn: async () => {
      try {
        const res = await apiFetch<{ settings: Record<string, unknown> }>(
          `/v1/settings/client/${encodeURIComponent(clientId)}`,
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
      await apiFetch(`/v1/settings/client/web`, {
        method: "PUT",
        body: JSON.stringify({
          settings: { ...settings, dismissed_announcements: updated },
        }),
      });
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

function AnnouncementBanner({
  announcement,
  onDismiss,
  onDontShowAgain,
}: {
  announcement: Announcement;
  onDismiss: () => void;
  onDontShowAgain: () => void;
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
        <span className="font-medium">{announcement.title}</span>
        {announcement.body && (
          <span className="ml-1.5">{announcement.body}</span>
        )}
      </div>
      {announcement.dismissible && (
        <div className="flex items-center gap-1 shrink-0">
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs opacity-70 hover:opacity-100"
            onClick={onDontShowAgain}
          >
            Don't show again
          </Button>
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
