import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  securityIpLookup,
  securityStuffingView,
  type SecurityEventRow,
} from "@/lib/admin";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

function outcomeBadge(outcome: string) {
  const ok = outcome === "success" || outcome === "sent";
  return (
    <Badge
      variant={ok ? "outline" : "destructive"}
      className="text-[10px] font-mono"
    >
      {outcome}
    </Badge>
  );
}

function EventsTable({ events }: { events: SecurityEventRow[] }) {
  const { formatDateTime } = useDateFormatters();
  return (
    <Card>
      <CardContent className="p-0">
        <table className="w-full">
          <thead>
            <tr className="border-b text-xs text-muted-foreground">
              <th className="py-2 px-3 text-left font-medium">When</th>
              <th className="py-2 px-3 text-left font-medium">Type</th>
              <th className="py-2 px-3 text-left font-medium">Outcome</th>
              <th className="py-2 px-3 text-left font-medium">Account</th>
              <th className="py-2 px-3 text-left font-medium">IP</th>
              <th className="py-2 px-3 text-left font-medium">Client</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id} className="border-b text-sm last:border-0">
                <td className="py-2 px-3 align-top whitespace-nowrap">
                  {formatDateTime(e.created_at)}
                </td>
                <td className="py-2 px-3 align-top">{e.event_type}</td>
                <td className="py-2 px-3 align-top">
                  {outcomeBadge(e.outcome)}
                </td>
                <td className="py-2 px-3 align-top font-mono text-xs text-muted-foreground">
                  {e.user_id ?? (
                    <span className="italic">unknown</span>
                  )}
                </td>
                <td className="py-2 px-3 align-top font-mono text-xs">
                  {e.ip ?? ""}
                </td>
                <td className="py-2 px-3 align-top text-xs text-muted-foreground max-w-[16rem] truncate">
                  {e.user_agent ?? ""}
                </td>
              </tr>
            ))}
            {events.length === 0 && (
              <tr>
                <td
                  colSpan={6}
                  className="py-6 px-3 text-center text-sm text-muted-foreground"
                >
                  No security events for this query.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

function IpLookup({
  target,
  setTarget,
}: {
  target: string;
  setTarget: (v: string) => void;
}) {
  const [reason, setReason] = useState("");

  const lookup = useMutation({
    mutationFn: () => securityIpLookup(target.trim(), reason.trim()),
  });
  const result = lookup.data;

  return (
    <section className="mb-8">
      <h2 className="text-sm font-medium mb-2">IP / subnet lookup</h2>
      <p className="text-sm text-muted-foreground max-w-prose mb-3">
        Everything the security log has seen from an exact address
        (<span className="font-mono">203.0.113.7</span>) or a CIDR subnet
        (<span className="font-mono">203.0.113.0/24</span>). This is an audited
        read of account-linked data, so a reason is recorded.
      </p>
      <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end mb-4">
        <div className="space-y-1">
          <Label htmlFor="ip-target" className="text-xs">
            IP or subnet
          </Label>
          <Input
            id="ip-target"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="203.0.113.7 or 203.0.113.0/24"
            className="font-mono text-xs"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="ip-reason" className="text-xs">
            Reason
          </Label>
          <Input
            id="ip-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why are you looking this up?"
            className="text-xs"
          />
        </div>
        <Button
          size="sm"
          disabled={!target.trim() || !reason.trim() || lookup.isPending}
          onClick={() => lookup.mutate()}
        >
          {lookup.isPending ? "Looking up..." : "Look up"}
        </Button>
      </div>

      {lookup.isError && (
        <p className="text-sm text-destructive mb-3">
          Lookup failed. Check the address or subnet is valid.
        </p>
      )}

      {result && (
        <>
          <div className="flex flex-wrap gap-2 mb-3 text-xs text-muted-foreground">
            <Badge variant="outline" className="text-[10px]">
              {result.event_count} events
            </Badge>
            <Badge variant="outline" className="text-[10px]">
              {result.distinct_account_ids.length} distinct accounts
            </Badge>
            <Badge variant="outline" className="text-[10px]">
              {result.signup_match_ids.length} signup-IP matches
            </Badge>
            {result.is_subnet && (
              <Badge variant="outline" className="text-[10px]">
                subnet query
              </Badge>
            )}
          </div>
          <EventsTable events={result.events} />
          <p className="text-xs text-muted-foreground max-w-prose mt-2">
            {result.note}
          </p>
        </>
      )}
    </section>
  );
}

function StuffingWatch({
  onPickIp,
}: {
  onPickIp: (ip: string) => void;
}) {
  const { formatDateTime } = useDateFormatters();
  const [hours, setHours] = useState(24);
  const [minFailures, setMinFailures] = useState(10);

  const opts = useMemo(
    () => ({ hours, min_failures: minFailures, limit: 50 }),
    [hours, minFailures],
  );
  const { data } = useQuery({
    queryKey: ["admin", "security", "stuffing", opts],
    queryFn: () => securityStuffingView(opts),
  });

  return (
    <section>
      <h2 className="text-sm font-medium mb-2">Credential-stuffing watch</h2>
      <p className="text-sm text-muted-foreground max-w-prose mb-3">
        IPs with the most failed logins in the window, ranked by how many
        distinct accounts each one targeted - the stuffing fingerprint. A high
        failure count against few accounts is brute force or scanning instead.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 max-w-md mb-4">
        <div className="space-y-1">
          <Label htmlFor="stuff-hours" className="text-xs">
            Window (hours)
          </Label>
          <Input
            id="stuff-hours"
            type="number"
            min={1}
            max={720}
            value={hours}
            onChange={(e) => setHours(Number(e.target.value) || 1)}
            className="text-xs"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="stuff-min" className="text-xs">
            Min failures
          </Label>
          <Input
            id="stuff-min"
            type="number"
            min={1}
            value={minFailures}
            onChange={(e) => setMinFailures(Number(e.target.value) || 1)}
            className="text-xs"
          />
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          <table className="w-full">
            <thead>
              <tr className="border-b text-xs text-muted-foreground">
                <th className="py-2 px-3 text-left font-medium">IP</th>
                <th className="py-2 px-3 text-right font-medium">
                  Distinct accounts
                </th>
                <th className="py-2 px-3 text-right font-medium">Failures</th>
                <th className="py-2 px-3 text-left font-medium">Last seen</th>
                <th className="py-2 px-3" />
              </tr>
            </thead>
            <tbody>
              {data?.offenders.map((o) => (
                <tr key={o.ip} className="border-b text-sm last:border-0">
                  <td className="py-2 px-3 align-top font-mono text-xs">
                    {o.ip}
                  </td>
                  <td className="py-2 px-3 align-top text-right tabular-nums">
                    {o.distinct_accounts}
                  </td>
                  <td className="py-2 px-3 align-top text-right tabular-nums">
                    {o.failures}
                  </td>
                  <td className="py-2 px-3 align-top whitespace-nowrap text-xs text-muted-foreground">
                    {formatDateTime(o.last_seen)}
                  </td>
                  <td className="py-2 px-3 align-top text-right">
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-[10px] h-6"
                      onClick={() => onPickIp(o.ip)}
                    >
                      Look up
                    </Button>
                  </td>
                </tr>
              ))}
              {data?.offenders.length === 0 && (
                <tr>
                  <td
                    colSpan={5}
                    className="py-6 px-3 text-center text-sm text-muted-foreground"
                  >
                    No IPs over the failure threshold in this window.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </section>
  );
}

export function AdminSecurityPage() {
  // Shared so "Look up" on a stuffing row prefills the lookup field.
  const [target, setTarget] = useState("");

  return (
    <>
      <PageHeader title="Security" />
      <p className="text-sm text-muted-foreground max-w-prose mb-6">
        Search the authentication event log by IP, and watch for
        credential-stuffing. Per-account timelines live on each user's detail
        view.
      </p>
      <IpLookup target={target} setTarget={setTarget} />
      <StuffingWatch onPickIp={setTarget} />
    </>
  );
}
