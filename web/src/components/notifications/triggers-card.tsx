import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { CofrontRedaction, NotificationChannel } from "@/types/api";

export function TriggersCard({
  channel,
  onChange,
}: {
  channel: NotificationChannel;
  onChange: (patch: Partial<NotificationChannel>) => void;
}) {
  const cofrontDisabled = channel.payload_sensitivity !== "full";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Triggers</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Row
          label="Member starts fronting"
          checked={channel.trigger_on_start}
          onChange={(v) => onChange({ trigger_on_start: v })}
        />
        <Row
          label="Member stops fronting"
          checked={channel.trigger_on_stop}
          onChange={(v) => onChange({ trigger_on_stop: v })}
        />
        <div className="space-y-2 rounded border bg-muted/30 px-3 py-2">
          <Row
            label="Co-front composition changes"
            description="Someone joins or leaves alongside a watched member."
            checked={channel.trigger_on_cofront_change}
            onChange={(v) => onChange({ trigger_on_cofront_change: v })}
          />
          {channel.trigger_on_cofront_change && (
            <div className="ml-6 space-y-1">
              <Label
                htmlFor="cofront-redaction-select"
                className="text-xs text-muted-foreground"
              >
                Co-fronter redaction
              </Label>
              <Select
                value={channel.cofront_redaction}
                onValueChange={(v) =>
                  onChange({ cofront_redaction: v as CofrontRedaction })
                }
                disabled={cofrontDisabled}
              >
                <SelectTrigger id="cofront-redaction-select" className="h-8 w-56">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="count">Count only ("1 other")</SelectItem>
                  <SelectItem value="someone">"Someone joined"</SelectItem>
                  <SelectItem value="suppress">
                    Suppress if any invisible
                  </SelectItem>
                </SelectContent>
              </Select>
              {cofrontDisabled && (
                <p className="text-xs text-muted-foreground">
                  Only meaningful when payload sensitivity is <em>full</em>.
                </p>
              )}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function Row({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-start gap-3 cursor-pointer">
      <Checkbox
        checked={checked}
        onCheckedChange={(v) => onChange(v === true)}
        className="mt-0.5"
      />
      <div className="space-y-0.5">
        <p className="text-sm font-medium leading-none">{label}</p>
        {description && (
          <p className="text-xs text-muted-foreground">{description}</p>
        )}
      </div>
    </label>
  );
}
