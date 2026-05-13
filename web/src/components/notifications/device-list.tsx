import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Smartphone, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  deletePushDevice,
  listPushDevices,
  updatePushDevice,
  type PushDevice,
} from "@/lib/devices";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";

const PLATFORM_LABELS: Record<PushDevice["platform"], string> = {
  fcm: "Android",
  apns_prod: "iOS",
  apns_dev: "iOS (dev)",
};

function deviceDisplayName(d: PushDevice): string {
  if (d.label) return d.label;
  const plat = PLATFORM_LABELS[d.platform] ?? d.platform;
  // install_id is opaque but a 6-char prefix is enough to distinguish
  // multiple unlabeled devices of the same platform.
  const tail = d.install_id ? ` (${d.install_id.slice(0, 6)})` : "";
  return `${plat} device${tail}`;
}

function relativeTime(iso: string): string {
  const now = Date.now();
  const then = new Date(iso).getTime();
  const secs = Math.max(0, Math.round((now - then) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

export function DeviceList() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["push-devices"],
    queryFn: listPushDevices,
  });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { enabled?: boolean; label?: string | null } }) =>
      updatePushDevice(id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["push-devices"] });
    },
  });
  const drop = useMutation({
    mutationFn: deletePushDevice,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["push-devices"] });
      toast.success("Device removed");
    },
  });

  function commitRename(d: PushDevice) {
    const next = editValue.trim();
    if (next === (d.label ?? "")) {
      setEditingId(null);
      return;
    }
    patch.mutate(
      { id: d.id, body: { label: next || null } },
      {
        onSuccess: () => setEditingId(null),
      },
    );
  }

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Your devices</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {[1, 2].map((i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </CardContent>
      </Card>
    );
  }

  if (!data || data.length === 0) {
    // No registered devices yet — don't render the card at all. The
    // mobile app populates this on first sign-in; nothing to manage
    // until then.
    return null;
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Smartphone className="size-4" />
          Your devices
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-xs text-muted-foreground">
          Mobile push channels ring every device you've signed into. Toggle
          one off to mute it without unregistering (e.g. silence the work
          phone over the weekend). Remove a device to drop its registration
          entirely.
        </p>
        <ul className="divide-y rounded-md border">
          {data.map((d) => (
            <li
              key={d.id}
              className="flex flex-wrap items-center gap-3 p-3 text-sm"
            >
              <div className="min-w-0 flex-1">
                {editingId === d.id ? (
                  <div className="flex items-center gap-2">
                    <Input
                      autoFocus
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commitRename(d);
                        if (e.key === "Escape") setEditingId(null);
                      }}
                      onBlur={() => commitRename(d)}
                      maxLength={80}
                      placeholder="Device name"
                      className="h-8 max-w-[16rem]"
                    />
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setEditingId(d.id);
                      setEditValue(d.label ?? "");
                    }}
                    className="text-left font-medium hover:underline"
                    title="Rename device"
                  >
                    {deviceDisplayName(d)}
                    <Pencil className="ml-1.5 inline size-3 text-muted-foreground" />
                  </button>
                )}
                <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline" className="font-normal">
                    {PLATFORM_LABELS[d.platform] ?? d.platform}
                  </Badge>
                  <span>Last seen {relativeTime(d.last_seen_at)}</span>
                  {!d.enabled && (
                    <span className="text-amber-600 dark:text-amber-400">
                      Muted
                    </span>
                  )}
                </div>
              </div>
              <Checkbox
                checked={d.enabled}
                disabled={patch.isPending}
                onCheckedChange={(v) =>
                  patch.mutate({ id: d.id, body: { enabled: v === true } })
                }
                aria-label={d.enabled ? "Mute this device" : "Unmute this device"}
              />
              <Button
                size="sm"
                variant="ghost"
                className="text-destructive-foreground"
                disabled={drop.isPending}
                onClick={() => {
                  if (
                    window.confirm(
                      `Remove "${deviceDisplayName(d)}" from your account? Push notifications will stop until you sign in on the device again.`,
                    )
                  ) {
                    drop.mutate(d.id);
                  }
                }}
                aria-label="Remove device"
              >
                <Trash2 className="size-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
