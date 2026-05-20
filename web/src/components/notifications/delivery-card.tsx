import { useQuery } from "@tanstack/react-query";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { getNotificationsServerConfig } from "@/lib/notifications";
import type { NotificationChannel, PayloadSensitivity } from "@/types/api";

export function DeliveryCard({
  channel,
  onChange,
}: {
  channel: NotificationChannel;
  onChange: (patch: Partial<NotificationChannel>) => void;
}) {
  const { data: serverCfg } = useQuery({
    queryKey: ["notifications", "server-config"],
    queryFn: getNotificationsServerConfig,
  });
  // Pushover channels using the shared deployment app token can't drop
  // debounce below the operator's floor — surface that here so the user
  // doesn't get rejected at save time.
  const usingSharedPushover =
    channel.destination_type === "pushover" &&
    !(channel.destination_config?.app_token);
  const debounceFloor =
    usingSharedPushover
      ? (serverCfg?.pushover.shared_app_min_debounce_seconds ?? 0)
      : 0;

  const quietEnabled = !!channel.quiet_hours;
  // Default new quiet-hours configs to the recipient's browser timezone —
  // the most useful guess. Existing channels keep whatever's stored.
  const browserTz = (() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
      return "UTC";
    }
  })();
  const qh =
    channel.quiet_hours ?? { start: "22:00", end: "07:00", tz: browserTz };

  // Full IANA list, alphabetised. Modern browsers all support
  // supportedValuesOf("timeZone"); older ones get a small fallback.
  const tzList = (() => {
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const fn = (Intl as any).supportedValuesOf;
      if (typeof fn === "function") return fn("timeZone") as string[];
    } catch {
      // fall through
    }
    return ["UTC", browserTz];
  })();

  return (
    <Card>
      <CardHeader>
        <CardTitle>Delivery</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="delivery-payload-sensitivity">Payload sensitivity</Label>
          <Select
            value={channel.payload_sensitivity}
            onValueChange={(v) =>
              onChange({ payload_sensitivity: v as PayloadSensitivity })
            }
          >
            <SelectTrigger id="delivery-payload-sensitivity">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="full">
                Full &mdash; include member names
              </SelectItem>
              <SelectItem value="minimal">
                Minimal &mdash; "someone started fronting"
              </SelectItem>
              <SelectItem value="bare">
                Bare &mdash; "a front changed"
              </SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="delivery-debounce">Debounce (seconds)</Label>
            <Input
              id="delivery-debounce"
              type="number"
              min={debounceFloor}
              max={86400}
              value={channel.debounce_seconds}
              aria-invalid={
                debounceFloor > 0 && channel.debounce_seconds < debounceFloor
              }
              onChange={(e) =>
                onChange({ debounce_seconds: Number(e.target.value || 0) })
              }
            />
            {debounceFloor > 0 && channel.debounce_seconds < debounceFloor ? (
              <p className="text-xs text-destructive">
                This instance requires at least {debounceFloor} seconds (
                {Math.round(debounceFloor / 60)} min) between deliveries on
                the shared Pushover app. Raise this value, or set your own
                Pushover app token to bypass the shared-app limit.
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                Minimum gap between deliveries on this channel.
                {debounceFloor > 0 && (
                  <>
                    {" "}
                    Shared Pushover app requires at least{" "}
                    {Math.round(debounceFloor / 60)} min.
                  </>
                )}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="delivery-aggregation">Aggregation window (seconds)</Label>
            <Input
              id="delivery-aggregation"
              type="number"
              min={0}
              max={86400}
              value={channel.aggregation_window_seconds}
              onChange={(e) =>
                onChange({
                  aggregation_window_seconds: Number(e.target.value || 0),
                })
              }
            />
            <p className="text-xs text-muted-foreground">
              0 = realtime. Higher batches multiple events into one.
            </p>
          </div>
        </div>

        <div className="space-y-2 rounded border bg-muted/30 px-3 py-2">
          <label className="flex items-center gap-2 cursor-pointer">
            <Checkbox
              checked={quietEnabled}
              onCheckedChange={(v) =>
                onChange({
                  quiet_hours:
                    v === true
                      ? { start: "22:00", end: "07:00", tz: "UTC" }
                      : null,
                })
              }
            />
            <span className="text-sm font-medium">Quiet hours</span>
          </label>
          {quietEnabled && (
            <div className="ml-6 grid gap-2 sm:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor="quiet-hours-start" className="text-xs">Start</Label>
                <Input
                  id="quiet-hours-start"
                  type="time"
                  value={qh.start}
                  onChange={(e) =>
                    onChange({ quiet_hours: { ...qh, start: e.target.value } })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="quiet-hours-end" className="text-xs">End</Label>
                <Input
                  id="quiet-hours-end"
                  type="time"
                  value={qh.end}
                  onChange={(e) =>
                    onChange({ quiet_hours: { ...qh, end: e.target.value } })
                  }
                />
              </div>
              <div className="col-span-2 space-y-1">
                <Label htmlFor="quiet-hours-tz" className="text-xs">Timezone</Label>
                <Select
                  value={qh.tz}
                  onValueChange={(v) =>
                    onChange({ quiet_hours: { ...qh, tz: v } })
                  }
                >
                  <SelectTrigger id="quiet-hours-tz">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="max-h-72">
                    {tzList.map((tz) => (
                      <SelectItem key={tz} value={tz}>
                        {tz}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <p className="col-span-2 text-xs text-muted-foreground">
                Events landing inside the window are deferred to the end
                time. DST transitions in the chosen timezone are honoured.
              </p>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
