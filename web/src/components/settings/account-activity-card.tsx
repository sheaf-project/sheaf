import { useQuery } from "@tanstack/react-query";
import {
  getMyAccountActivity,
  type AccountActivityEvent,
} from "@/lib/account-activity";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const ACTION_LABELS: Record<string, string> = {
  password_changed: "Password changed",
  email_change_requested: "Email change requested",
  email_changed: "Email changed",
  totp_enabled: "Two-factor enabled",
  totp_disabled: "Two-factor disabled",
  recovery_codes_regenerated: "Recovery codes regenerated",
  api_key_created: "API key created",
  api_key_revoked: "API key revoked",
  session_revoked: "Session signed out",
  trusted_device_revoked: "Trusted device removed",
  account_deletion_scheduled: "Account deletion scheduled",
  account_deletion_cancelled: "Account deletion cancelled",
  data_export_requested: "Data export requested",
  import_completed: "Import completed",
  export_ready: "Export ready",
};

function actorLabel(actorType: AccountActivityEvent["actor_type"]): string {
  return actorType === "system" ? "System" : "You";
}

function formatDetail(detail: Record<string, unknown> | null): string {
  if (!detail) return "";
  const parts: string[] = [];
  for (const [k, v] of Object.entries(detail)) {
    parts.push(`${k}: ${JSON.stringify(v)}`);
  }
  return parts.join(", ");
}

export function AccountActivityCard() {
  const { formatDateTime } = useDateFormatters();
  const { data: events, isLoading } = useQuery({
    queryKey: ["account-activity", "me"],
    queryFn: () => getMyAccountActivity(),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Account activity</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground max-w-prose">
          Significant and automated actions on your account. Member edits are
          not listed here.
        </p>
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading...</p>
        )}
        {events && events.length === 0 && (
          <p className="text-sm text-muted-foreground">No recent activity.</p>
        )}
        {events && events.length > 0 && (
          <div className="space-y-3">
            {events.map((e: AccountActivityEvent) => (
              <div
                key={e.id}
                className="space-y-1 rounded-md border p-3 text-sm"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="text-[10px]">
                      {ACTION_LABELS[e.action] ?? e.action}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {actorLabel(e.actor_type)}
                      {e.target_label ? ` - ${e.target_label}` : ""}
                    </span>
                  </div>
                  <span className="text-xs text-muted-foreground whitespace-nowrap">
                    {formatDateTime(e.created_at)}
                  </span>
                </div>
                {e.detail && Object.keys(e.detail).length > 0 && (
                  <p className="font-mono text-xs text-muted-foreground">
                    {formatDetail(e.detail)}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
