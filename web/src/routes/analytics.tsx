import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useMembers } from "@/hooks/use-members";
import { getFrontingAnalytics } from "@/lib/analytics";
import { ColorDot } from "@/components/color-dot";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { Member, MemberFrontingStats } from "@/types/api";

// Recharts can't pull from our `var(--primary)` token directly because
// our theme uses oklch() values that don't compose with hsl() the way
// shadcn's stock CSS would. Hardcode chart colours that read well on both
// light and dark backgrounds; tweak alongside the theme if it ever shifts.
const CHART_BAR = "#8b5cf6"; // violet 500, neutral hue for "anyone fronting"
const CHART_GRID = "rgb(127 127 127 / 0.18)";
const AXIS_TICK = { fill: "currentColor", fontSize: 12, opacity: 0.7 } as const;
const AXIS_LINE = { stroke: "currentColor", opacity: 0.2 } as const;
const TOOLTIP_STYLE = {
  background: "var(--popover)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  color: "var(--popover-foreground)",
  fontSize: 12,
} as const;

type WindowChoice = "7d" | "30d" | "90d" | "365d";

const WINDOW_DAYS: Record<WindowChoice, number> = {
  "7d": 7,
  "30d": 30,
  "90d": 90,
  "365d": 365,
};

function browserTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function formatHours(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const hours = seconds / 3600;
  if (hours < 1) return `${Math.round(seconds / 60)}m`;
  if (hours < 10) return `${hours.toFixed(1)}h`;
  return `${Math.round(hours)}h`;
}

export function AnalyticsPage() {
  const [windowChoice, setWindowChoice] = useState<WindowChoice>("30d");
  const [showCustomFronts, setShowCustomFronts] = useState(false);
  const tz = browserTz();

  const { since, until } = useMemo(() => {
    const u = new Date();
    const s = new Date(u.getTime() - WINDOW_DAYS[windowChoice] * 86400 * 1000);
    return { since: s, until: u };
  }, [windowChoice]);

  const { data: members } = useMembers();
  const { data, isLoading } = useQuery({
    queryKey: ["analytics", "fronting", windowChoice, tz],
    queryFn: () => getFrontingAnalytics({ since, until, tz }),
    // Refresh on focus is overkill for analytics; the user picks a window
    // and reads it. Skip the staleness dance.
    refetchOnWindowFocus: false,
  });

  const memberById = useMemo(
    () => new Map<string, Member>((members ?? []).map((m) => [m.id, m])),
    [members],
  );

  const filteredStats = useMemo(() => {
    if (!data) return [];
    return data.members.filter(
      (s) => showCustomFronts || !s.is_custom_front,
    );
  }, [data, showCustomFronts]);

  return (
    <>
      <PageHeader title="Analytics" />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {(Object.keys(WINDOW_DAYS) as WindowChoice[]).map((choice) => (
          <Button
            key={choice}
            size="sm"
            variant={windowChoice === choice ? "default" : "outline"}
            onClick={() => setWindowChoice(choice)}
          >
            {choice === "365d" ? "1 year" : `${WINDOW_DAYS[choice]} days`}
          </Button>
        ))}
        <span className="ml-auto text-xs text-muted-foreground">
          Times shown in {tz}
        </span>
      </div>

      {isLoading || !data ? (
        <div className="grid gap-4 md:grid-cols-2">
          <Skeleton className="h-72" />
          <Skeleton className="h-72" />
        </div>
      ) : filteredStats.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No fronting data in this window. Start a front to see stats here.
        </p>
      ) : (
        <div className="space-y-6">
          <TotalsCard
            stats={filteredStats}
            memberById={memberById}
            showCustomFronts={showCustomFronts}
            onToggleCustomFronts={setShowCustomFronts}
            anyCustomFronts={data.members.some((s) => s.is_custom_front)}
          />
          <HourOfDayCard stats={filteredStats} memberById={memberById} />
          <DetailTable stats={filteredStats} memberById={memberById} />
        </div>
      )}
    </>
  );
}

function TotalsCard({
  stats,
  memberById,
  showCustomFronts,
  onToggleCustomFronts,
  anyCustomFronts,
}: {
  stats: MemberFrontingStats[];
  memberById: Map<string, Member>;
  showCustomFronts: boolean;
  onToggleCustomFronts: (v: boolean) => void;
  anyCustomFronts: boolean;
}) {
  const rows = stats
    .filter((s) => s.total_seconds > 0)
    .map((s) => {
      const member = memberById.get(s.member_id);
      return {
        id: s.member_id,
        member,
        name: member?.display_name || member?.name || "Unknown",
        hours: s.total_seconds / 3600,
        percent: s.percent_of_window,
      };
    });

  if (rows.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Time fronting</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Nobody fronted in this window.
          </p>
        </CardContent>
      </Card>
    );
  }

  const maxHours = Math.max(...rows.map((r) => r.hours), 1);

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-base">Time fronting</CardTitle>
        {anyCustomFronts && (
          <label className="flex items-center gap-2 text-xs cursor-pointer text-muted-foreground">
            <input
              type="checkbox"
              checked={showCustomFronts}
              onChange={(e) => onToggleCustomFronts(e.target.checked)}
              className="h-3.5 w-3.5"
            />
            Show custom fronts
          </label>
        )}
      </CardHeader>
      <CardContent>
        {/* Recharts' bar charts are awkward to theme well — categorical
            colours (member colours) clash with the dark backdrop, and the
            text rendering needs prop juggling. Hand-rolling a horizontal
            bar with divs gives full control over typography, member
            colour as the bar fill, and lets us show the value inline. */}
        <div className="space-y-2">
          {rows.map((row) => {
            const widthPct = (row.hours / maxHours) * 100;
            const barColor = row.member?.color ?? "var(--primary)";
            return (
              <div
                key={row.id}
                className="grid grid-cols-[10rem_1fr_auto] items-center gap-3 text-sm"
                title={`${row.hours.toFixed(2)}h (${row.percent.toFixed(1)}%)`}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <ColorDot color={row.member?.color ?? null} />
                  {row.member?.emoji && (
                    <span className="shrink-0">{row.member.emoji}</span>
                  )}
                  <span className="truncate">{row.name}</span>
                </div>
                <div className="relative h-6 rounded-md bg-muted/40 overflow-hidden">
                  <div
                    className="absolute inset-y-0 left-0 rounded-md"
                    style={{
                      width: `${widthPct}%`,
                      backgroundColor: barColor,
                      opacity: 0.85,
                    }}
                  />
                </div>
                <div className="font-mono text-xs text-muted-foreground tabular-nums whitespace-nowrap">
                  {formatHours(row.hours * 3600)} · {row.percent.toFixed(1)}%
                </div>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

function HourOfDayCard({
  stats,
  memberById,
}: {
  stats: MemberFrontingStats[];
  memberById: Map<string, Member>;
}) {
  // Sum across all selected members per hour. Heatmap-style breakdown by
  // member is overkill at this scale; the aggregate "when does anyone in
  // your system front" view is more useful.
  const data = useMemo(() => {
    const totals = Array.from({ length: 24 }, () => 0);
    for (const s of stats) {
      for (let h = 0; h < 24; h++) {
        totals[h] += s.hour_of_day_seconds[h] ?? 0;
      }
    }
    return totals.map((seconds, hour) => ({
      hour,
      hours: seconds / 3600,
      label: hour.toString().padStart(2, "0"),
    }));
  }, [stats]);

  // Per-member breakdown for the tooltip — keeps the chart readable while
  // letting the user drill into "who was up at 3am" by hovering.
  const perHourBreakdown = useMemo(() => {
    const out = Array.from({ length: 24 }, () => [] as Array<{ name: string; hours: number }>);
    for (const s of stats) {
      const member = memberById.get(s.member_id);
      const name = member?.display_name || member?.name || "Unknown";
      for (let h = 0; h < 24; h++) {
        const secs = s.hour_of_day_seconds[h] ?? 0;
        if (secs > 0) {
          out[h].push({ name, hours: secs / 3600 });
        }
      }
    }
    for (const list of out) list.sort((a, b) => b.hours - a.hours);
    return out;
  }, [stats, memberById]);

  const totalAcross = data.reduce((acc, d) => acc + d.hours, 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">When you front</CardTitle>
      </CardHeader>
      <CardContent>
        {totalAcross === 0 ? (
          <p className="text-sm text-muted-foreground">
            No fronting time to break down by hour.
          </p>
        ) : (
          <div className="text-foreground">
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID} vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={AXIS_TICK}
                  axisLine={AXIS_LINE}
                  tickLine={false}
                  interval={1}
                />
                <YAxis
                  tickFormatter={(v) => `${Number(v).toFixed(1)}h`}
                  tick={AXIS_TICK}
                  axisLine={false}
                  tickLine={false}
                  width={48}
                />
                <Tooltip
                  cursor={{ fill: "currentColor", fillOpacity: 0.06 }}
                  contentStyle={TOOLTIP_STYLE}
                  content={({ active, payload }) => {
                    if (!active || !payload?.length) return null;
                    const row = payload[0].payload as {
                      hour: number;
                      hours: number;
                      label: string;
                    };
                    const breakdown = perHourBreakdown[row.hour] ?? [];
                    return (
                      <div className="rounded-md border bg-popover p-2 text-xs text-popover-foreground shadow-md">
                        <p className="font-medium">
                          {row.label}:00 — {row.label}:59
                        </p>
                        <p className="text-muted-foreground">
                          {row.hours.toFixed(2)}h total
                        </p>
                        {breakdown.length > 0 && (
                          <div className="mt-1 space-y-0.5">
                            {breakdown.slice(0, 5).map((entry) => (
                              <div
                                key={entry.name}
                                className="flex justify-between gap-3"
                              >
                                <span>{entry.name}</span>
                                <span className="text-muted-foreground">
                                  {entry.hours.toFixed(2)}h
                                </span>
                              </div>
                            ))}
                            {breakdown.length > 5 && (
                              <p className="text-muted-foreground">
                                +{breakdown.length - 5} more
                              </p>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  }}
                />
                <Bar dataKey="hours" fill={CHART_BAR} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DetailTable({
  stats,
  memberById,
}: {
  stats: MemberFrontingStats[];
  memberById: Map<string, Member>;
}) {
  const rows = stats.filter((s) => s.total_seconds > 0);
  if (rows.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Per-member detail</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/30 text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-4 py-2 text-left font-medium">Member</th>
                <th className="px-4 py-2 text-right font-medium">Total</th>
                <th className="px-4 py-2 text-right font-medium">% of window</th>
                <th className="px-4 py-2 text-right font-medium">Sessions</th>
                <th className="px-4 py-2 text-right font-medium">Longest</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => {
                const member = memberById.get(s.member_id);
                return (
                  <tr key={s.member_id} className="border-b last:border-b-0">
                    <td className="px-4 py-2">
                      <span className="inline-flex items-center gap-2">
                        {member?.emoji && <span>{member.emoji}</span>}
                        <span>{member?.display_name || member?.name || "Unknown"}</span>
                        {s.is_custom_front && (
                          <span className="rounded-sm border px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
                            custom front
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right font-mono">
                      {formatHours(s.total_seconds)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono">
                      {s.percent_of_window.toFixed(1)}%
                    </td>
                    <td className="px-4 py-2 text-right font-mono">
                      {s.session_count}
                    </td>
                    <td className="px-4 py-2 text-right font-mono">
                      {formatHours(s.longest_session_seconds)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
