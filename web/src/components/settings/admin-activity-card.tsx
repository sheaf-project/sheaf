import { Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  describeAdminAction,
  getMyAdminActivity,
  type UserAdminActivityEvent,
} from "@/lib/admin";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const ACRONYMS: Record<string, string> = {
  api: "API",
  id: "ID",
  ip: "IP",
  totp: "TOTP",
};

const ISO_DATETIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;
const ENUM_VALUE = /^[a-z]+(_[a-z]+)*$/;

function humanize(s: string): string {
  const out = s
    .split("_")
    .map((w) => ACRONYMS[w] ?? w)
    .join(" ");
  return out.charAt(0).toUpperCase() + out.slice(1);
}

type DateTimeFormatter = (d: string) => string;

function formatValue(v: unknown, fmt: DateTimeFormatter): string {
  if (v === null || v === undefined) return "None";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return v.toLocaleString();
  if (typeof v === "string") {
    if (ISO_DATETIME.test(v)) return fmt(v);
    if (ENUM_VALUE.test(v)) return humanize(v);
    return v;
  }
  if (Array.isArray(v)) {
    if (v.length === 0) return "None";
    return v
      .map((x) =>
        x && typeof x === "object" && typeof x.name === "string"
          ? x.name
          : formatValue(x, fmt),
      )
      .join(", ");
  }
  return Object.entries(v as Record<string, unknown>)
    .map(([k, x]) => `${humanize(k)}: ${formatValue(x, fmt)}`)
    .join(", ");
}

function diffRows(
  before: Record<string, unknown> | null,
  after: Record<string, unknown> | null,
  fmt: DateTimeFormatter,
): { label: string; value: string }[] {
  const keys = new Set([
    ...Object.keys(before ?? {}),
    ...Object.keys(after ?? {}),
  ]);
  return [...keys].map((k) => {
    const x = before && k in before ? formatValue(before[k], fmt) : null;
    const y = after && k in after ? formatValue(after[k], fmt) : null;
    let value = `${x} → ${y}`;
    if (x === null || x === y) value = y ?? "";
    else if (y === null) value = `was ${x}`;
    return { label: humanize(k), value };
  });
}

export function AdminActivityCard() {
  const { formatDateTime } = useDateFormatters();
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
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <span>An administrator {describeAdminAction(e.action)}</span>
                    <Badge variant="outline" className="text-[10px]">
                      {e.action}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {e.admin_email ?? "(admin deleted)"}
                    </span>
                  </div>
                  <span className="text-xs text-muted-foreground whitespace-nowrap">
                    {formatDateTime(e.created_at)}
                  </span>
                </div>
                {e.reason && (
                  <p className="italic text-xs text-muted-foreground">
                    Reason: "{e.reason}"
                  </p>
                )}
                {(e.before_json || e.after_json) && (
                  <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs">
                    {diffRows(e.before_json, e.after_json, formatDateTime).map((r) => (
                      <Fragment key={r.label}>
                        <dt className="text-muted-foreground">{r.label}</dt>
                        <dd className="wrap-anywhere">{r.value}</dd>
                      </Fragment>
                    ))}
                  </dl>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
