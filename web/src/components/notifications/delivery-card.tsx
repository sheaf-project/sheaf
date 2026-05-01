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
import type { NotificationChannel, PayloadSensitivity } from "@/types/api";

export function DeliveryCard({
  channel,
  onChange,
}: {
  channel: NotificationChannel;
  onChange: (patch: Partial<NotificationChannel>) => void;
}) {
  const quietEnabled = !!channel.quiet_hours;
  const qh = channel.quiet_hours ?? { start: "22:00", end: "07:00", tz: "UTC" };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Delivery</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label>Payload sensitivity</Label>
          <Select
            value={channel.payload_sensitivity}
            onValueChange={(v) =>
              onChange({ payload_sensitivity: v as PayloadSensitivity })
            }
          >
            <SelectTrigger>
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
            <Label>Debounce (seconds)</Label>
            <Input
              type="number"
              min={0}
              max={86400}
              value={channel.debounce_seconds}
              onChange={(e) =>
                onChange({ debounce_seconds: Number(e.target.value || 0) })
              }
            />
            <p className="text-xs text-muted-foreground">
              Minimum gap between deliveries on this channel.
            </p>
          </div>

          <div className="space-y-2">
            <Label>Aggregation window (seconds)</Label>
            <Input
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
                <Label className="text-xs">Start</Label>
                <Input
                  type="time"
                  value={qh.start}
                  onChange={(e) =>
                    onChange({ quiet_hours: { ...qh, start: e.target.value } })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">End</Label>
                <Input
                  type="time"
                  value={qh.end}
                  onChange={(e) =>
                    onChange({ quiet_hours: { ...qh, end: e.target.value } })
                  }
                />
              </div>
              <p className="col-span-2 text-xs text-muted-foreground">
                In UTC for v1. Events landing inside the window are deferred to
                the end time.
              </p>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
