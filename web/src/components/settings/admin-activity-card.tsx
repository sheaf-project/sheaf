import { useQuery } from "@tanstack/react-query";
import { getMyAdminActivity, type UserAdminActivityEvent } from "@/lib/admin";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

function formatDiff(
  before: Record<string, unknown> | null,
  after: Record<string, unknown> | null,
): string {
  if (!before && !after) return "";
  const keys = new Set([
    ...Object.keys(before ?? {}),
    ...Object.keys(after ?? {}),
  ]);
  const parts: string[] = [];
  for (const k of keys) {
    parts.push(`${k}: ${JSON.stringify(before?.[k])} -> ${JSON.stringify(after?.[k])}`);
  }
  return parts.join(", ");
}

export function AdminActivityCard() {
  const { data: events, isLoading } = useQuery({
    queryKey: ["admin-activity", "me"],
    queryFn: () => getMyAdminActivity(),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Admin activity on your account</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground max-w-prose">
          Every state-changing action an administrator takes against your
          account is recorded here. The admin's email, the action, the
          before/after values for any changed fields, and any reason text
          they entered are visible to you.
        </p>
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading...</p>
        )}
        {events && events.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No admin actions have been taken against your account.
          </p>
        )}
        {events && events.length > 0 && (
          <div className="space-y-3">
            {events.map((e: UserAdminActivityEvent) => (
              <div
                key={e.id}
                className="space-y-1 rounded-md border p-3 text-sm"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="text-[10px]">
                      {e.action}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {e.admin_email ?? "(admin deleted)"}
                    </span>
                  </div>
                  <span className="text-xs text-muted-foreground whitespace-nowrap">
                    {new Date(e.created_at).toLocaleString()}
                  </span>
                </div>
                {e.reason && (
                  <p className="italic text-xs text-muted-foreground">
                    Reason: "{e.reason}"
                  </p>
                )}
                {(e.before_json || e.after_json) && (
                  <p className="font-mono text-xs text-muted-foreground">
                    {formatDiff(e.before_json, e.after_json)}
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
