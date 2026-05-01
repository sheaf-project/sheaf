import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useReceiving,
  useUnsubscribeReceiving,
} from "@/hooks/use-notifications";
import type { ReceivingChannelView } from "@/types/api";

import { destinationLabel } from "./destination-meta";
import { DestinationIcon } from "./destination-icon";

const STATE_LABELS: Record<string, { label: string; tone: string }> = {
  active: { label: "Active", tone: "text-emerald-600 dark:text-emerald-400" },
  disabled: { label: "Unsubscribed", tone: "text-muted-foreground" },
  pending_registration: {
    label: "Pending",
    tone: "text-amber-600 dark:text-amber-400",
  },
};

export function ReceivingList() {
  const { data, isLoading } = useReceiving();
  const unsub = useUnsubscribeReceiving();

  if (isLoading) {
    return (
      <div className="space-y-2">
        {[1, 2].map((i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <Card>
        <CardContent className="p-6">
          <p className="text-sm text-muted-foreground">
            You're not receiving any notifications. When you redeem an
            activation link from another system's owner while signed in, the
            channel will appear here so you can manage it without juggling
            management URLs.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-2">
      {data.map((c) => (
        <ReceivingRow key={c.channel_id} channel={c} onUnsubscribe={unsub.mutate} />
      ))}
    </div>
  );
}

function ReceivingRow({
  channel,
  onUnsubscribe,
}: {
  channel: ReceivingChannelView;
  onUnsubscribe: (channelId: string) => void;
}) {
  const state = STATE_LABELS[channel.destination_state] ?? {
    label: channel.destination_state,
    tone: "text-muted-foreground",
  };
  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-4">
        <DestinationIcon
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          type={channel.destination_type as any}
          className="h-5 w-5 text-muted-foreground"
        />
        <div className="flex-1 min-w-0">
          <p className="font-medium truncate">{channel.channel_name}</p>
          <p className="text-xs text-muted-foreground">
            {channel.system_label
              ? `from ${channel.system_label} · `
              : ""}
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            {destinationLabel(channel.destination_type as any)}
            {channel.last_delivered_at && (
              <>
                {" · last delivery "}
                {new Date(channel.last_delivered_at).toLocaleString()}
              </>
            )}
          </p>
        </div>
        <span className={`text-xs font-medium ${state.tone}`}>
          {state.label}
        </span>
        {channel.destination_state === "active" && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              if (
                confirm(
                  `Unsubscribe from "${channel.channel_name}"? You'll stop receiving notifications. The owner sees the channel as disabled but isn't told who unsubscribed.`,
                )
              ) {
                onUnsubscribe(channel.channel_id);
              }
            }}
          >
            Unsubscribe
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
