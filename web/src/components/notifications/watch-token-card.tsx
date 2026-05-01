import { useState } from "react";
import { Link } from "react-router";
import { Pause, Play, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  useChannels,
  useRevokeWatchToken,
  useToggleChannel,
} from "@/hooks/use-notifications";
import type {
  ChannelCreateResponse,
  NotificationChannel,
  WatchToken,
} from "@/types/api";

import { ActivationLinkModal } from "./activation-link-modal";
import { DestinationIcon } from "./destination-icon";
import { destinationLabel } from "./destination-meta";
import { NewChannelDialog } from "./new-channel-dialog";

const STATE_LABELS: Record<string, { label: string; tone: string }> = {
  active: { label: "Active", tone: "text-emerald-600 dark:text-emerald-400" },
  pending_registration: {
    label: "Pending",
    tone: "text-amber-600 dark:text-amber-400",
  },
  disabled: { label: "Disabled", tone: "text-muted-foreground" },
  pending_verification: {
    label: "Verifying",
    tone: "text-amber-600 dark:text-amber-400",
  },
  declined_or_expired: {
    label: "Expired",
    tone: "text-destructive",
  },
};

export function WatchTokenCard({
  token,
  systemId,
}: {
  token: WatchToken;
  systemId: string;
}) {
  const { data: channels } = useChannels(token.id);
  const revoke = useRevokeWatchToken(systemId);
  const [showNew, setShowNew] = useState(false);
  const [activationModal, setActivationModal] =
    useState<ChannelCreateResponse | null>(null);

  const isRevoked = token.revoked_at !== null;

  return (
    <Card className={isRevoked ? "opacity-60" : ""}>
      <CardContent className="space-y-4 p-5">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-base font-medium">
              {token.label ?? "Unnamed watcher"}
            </p>
            <p className="text-xs text-muted-foreground">
              {channels?.length ?? token.channel_count} channel
              {(channels?.length ?? token.channel_count) === 1 ? "" : "s"}
              {isRevoked ? " · revoked" : ""}
            </p>
          </div>
          {!isRevoked && (
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowNew(true)}
              >
                <Plus className="mr-1 h-4 w-4" /> Channel
              </Button>
              <Button
                size="icon"
                variant="ghost"
                aria-label="Revoke watcher"
                onClick={() => {
                  if (
                    confirm(
                      `Revoke "${token.label ?? "this watcher"}"? Existing channels stop delivering immediately. This is reversible by editing the watcher's revoked state.`,
                    )
                  ) {
                    revoke.mutate(token.id);
                  }
                }}
              >
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </div>
          )}
        </div>

        {channels && channels.length > 0 ? (
          <div className="space-y-1.5">
            {channels.map((c) => (
              <ChannelRow key={c.id} channel={c} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No channels yet. Click <strong>Channel</strong> to add one.
          </p>
        )}
      </CardContent>

      <NewChannelDialog
        open={showNew}
        onOpenChange={setShowNew}
        tokenId={token.id}
        onCreated={(resp) => {
          if (resp.activation_url) {
            setActivationModal(resp);
          }
        }}
      />
      <ActivationLinkModal
        open={!!activationModal}
        onOpenChange={(open) => !open && setActivationModal(null)}
        url={activationModal?.activation_url ?? null}
        expiresAt={activationModal?.activation_expires_at ?? null}
        channelName={activationModal?.channel.name ?? "the recipient"}
      />
    </Card>
  );
}

function ChannelRow({ channel }: { channel: NotificationChannel }) {
  const toggle = useToggleChannel();
  const state = STATE_LABELS[channel.destination_state] ?? {
    label: channel.destination_state,
    tone: "text-muted-foreground",
  };

  // Pause/resume only meaningful for channels that have actually been
  // activated (not pending_registration / declined). Pending registration
  // is reachable from the channel detail page via re-issue.
  const canToggle =
    channel.destination_state === "active" ||
    channel.destination_state === "disabled";

  return (
    <div className="flex items-center justify-between rounded-md border bg-background px-3 py-2 hover:bg-accent/40 transition-colors">
      <Link
        to={`/notifications/${channel.id}`}
        className="flex items-center gap-3 min-w-0 flex-1"
      >
        <DestinationIcon
          type={channel.destination_type}
          className="h-4 w-4 text-muted-foreground"
        />
        <div className="min-w-0">
          <p className="text-sm font-medium truncate">{channel.name}</p>
          <p className="text-xs text-muted-foreground">
            {destinationLabel(channel.destination_type)}
          </p>
        </div>
      </Link>
      <div className="flex items-center gap-2 ml-2">
        <span className={`text-xs font-medium ${state.tone}`}>
          {state.label}
        </span>
        {canToggle && (
          <Button
            size="icon"
            variant="ghost"
            className="h-7 w-7"
            aria-label={
              channel.destination_state === "active" ? "Pause" : "Resume"
            }
            disabled={toggle.isPending}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              toggle.mutate({
                channelId: channel.id,
                enable: channel.destination_state !== "active",
              });
            }}
          >
            {channel.destination_state === "active" ? (
              <Pause className="h-3.5 w-3.5" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
          </Button>
        )}
      </div>
    </div>
  );
}
