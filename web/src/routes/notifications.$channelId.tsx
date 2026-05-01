import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ArrowLeft, Copy, Pause, Play, RefreshCw, Send, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ActivationLinkModal } from "@/components/notifications/activation-link-modal";
import { DeliveryCard } from "@/components/notifications/delivery-card";
import { DestinationIcon } from "@/components/notifications/destination-icon";
import { destinationLabel } from "@/components/notifications/destination-meta";
import {
  L1Card,
  L2Card,
  L3Card,
} from "@/components/notifications/layer-cards";
import { LivePreviewCard } from "@/components/notifications/live-preview-card";
import { TriggersCard } from "@/components/notifications/triggers-card";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useChannel,
  useDeleteChannel,
  useDuplicateChannel,
  useReissueActivation,
  useSendTest,
  useToggleChannel,
  useUpdateChannel,
} from "@/hooks/use-notifications";
import type {
  ChannelCreateResponse,
  ChannelUpdate,
  NotificationChannel,
} from "@/types/api";

const DRAFT_KEYS: (keyof NotificationChannel)[] = [
  "base_all_members",
  "base_include_private",
  "trigger_on_start",
  "trigger_on_stop",
  "trigger_on_cofront_change",
  "cofront_redaction",
  "payload_sensitivity",
  "debounce_seconds",
  "aggregation_window_seconds",
  "quiet_hours",
  "group_rules",
  "member_rules",
];

function buildDraft(channel: NotificationChannel): ChannelUpdate {
  const draft: ChannelUpdate = {};
  for (const k of DRAFT_KEYS) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (draft as any)[k] = channel[k] as any;
  }
  return draft;
}

function isDraftDirty(
  channel: NotificationChannel,
  draft: ChannelUpdate,
): boolean {
  for (const k of DRAFT_KEYS) {
    if (
      JSON.stringify(channel[k as keyof NotificationChannel]) !==
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      JSON.stringify((draft as any)[k])
    ) {
      return true;
    }
  }
  return false;
}

export function NotificationChannelPage() {
  const { channelId } = useParams<{ channelId: string }>();
  const navigate = useNavigate();
  const { data: channel, isLoading } = useChannel(channelId);
  const update = useUpdateChannel(channelId);
  const del = useDeleteChannel();
  const duplicate = useDuplicateChannel();
  const reissue = useReissueActivation();
  const sendTest = useSendTest();
  const toggle = useToggleChannel();

  const [draftState, setDraftState] = useState<{
    channelUpdatedAt: string | null;
    draft: ChannelUpdate;
  }>({ channelUpdatedAt: null, draft: {} });
  const [activationModal, setActivationModal] =
    useState<ChannelCreateResponse | null>(null);
  const [reissueModal, setReissueModal] = useState<{
    url: string;
    expires: string;
  } | null>(null);

  // Re-sync draft with the channel whenever the server-known updated_at
  // changes (after-save invalidation, initial load). Using inline-derived
  // state instead of useEffect avoids the cascading-render lint and is the
  // recommended pattern for "reset state when prop changes".
  let draft = draftState.draft;
  if (channel && draftState.channelUpdatedAt !== channel.updated_at) {
    draft = buildDraft(channel);
    setDraftState({ channelUpdatedAt: channel.updated_at, draft });
  }

  const dirty = useMemo(
    () => (channel ? isDraftDirty(channel, draft) : false),
    [channel, draft],
  );

  if (isLoading || !channel) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-12" />
        <Skeleton className="h-40" />
        <Skeleton className="h-40" />
      </div>
    );
  }

  function patch(p: Partial<NotificationChannel>) {
    setDraftState((s) => ({ ...s, draft: { ...s.draft, ...p } }));
  }

  function save() {
    update.mutate(draft);
  }

  function reset() {
    if (channel) {
      setDraftState({
        channelUpdatedAt: channel.updated_at,
        draft: buildDraft(channel),
      });
    }
  }

  return (
    <>
      <div className="mb-2">
        <Button asChild variant="ghost" size="sm">
          <Link to="/notifications">
            <ArrowLeft className="mr-1 h-4 w-4" /> Back to notifications
          </Link>
        </Button>
      </div>

      <PageHeader title={channel.name}>
        {(channel.destination_state === "active" ||
          channel.destination_state === "disabled") && (
          <Button
            variant="outline"
            size="sm"
            disabled={toggle.isPending}
            onClick={() =>
              toggle.mutate({
                channelId: channel.id,
                enable: channel.destination_state !== "active",
              })
            }
          >
            {channel.destination_state === "active" ? (
              <>
                <Pause className="mr-1 h-4 w-4" /> Pause
              </>
            ) : (
              <>
                <Play className="mr-1 h-4 w-4" /> Resume
              </>
            )}
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          onClick={() => sendTest.mutate(channel.id)}
          disabled={
            sendTest.isPending || channel.destination_state !== "active"
          }
        >
          <Send className="mr-1 h-4 w-4" /> Send test
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() =>
            duplicate.mutate(channel.id, {
              onSuccess: (resp) => {
                if (resp.activation_url) setActivationModal(resp);
                navigate(`/notifications/${resp.channel.id}`);
              },
            })
          }
        >
          <Copy className="mr-1 h-4 w-4" /> Duplicate
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            if (
              confirm(
                `Delete channel "${channel.name}"? This stops deliveries immediately and removes the channel.`,
              )
            ) {
              del.mutate(channel.id, {
                onSuccess: () => navigate("/notifications"),
              });
            }
          }}
        >
          <Trash2 className="mr-1 h-4 w-4 text-destructive" />
          Delete
        </Button>
      </PageHeader>

      <Card className="mb-4">
        <CardContent className="flex flex-wrap items-center gap-4 py-3">
          <div className="flex items-center gap-2">
            <DestinationIcon
              type={channel.destination_type}
              className="h-4 w-4 text-muted-foreground"
            />
            <span className="text-sm">
              {destinationLabel(channel.destination_type)}
            </span>
          </div>
          <span className="text-sm text-muted-foreground">
            State: <strong>{channel.destination_state.replace("_", " ")}</strong>
          </span>
          {channel.destination_state === "pending_registration" &&
            channel.destination_type === "web_push" && (
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  reissue.mutate(channel.id, {
                    onSuccess: (resp) => {
                      setReissueModal({
                        url: resp.activation_url,
                        expires: resp.activation_expires_at,
                      });
                      toast.success("Fresh activation link issued");
                    },
                  })
                }
              >
                <RefreshCw className="mr-1 h-4 w-4" /> Re-issue link
              </Button>
            )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <TriggersCard
          channel={{ ...channel, ...draft } as NotificationChannel}
          onChange={patch}
        />
        <L1Card
          channel={{ ...channel, ...draft } as NotificationChannel}
          onChange={patch}
        />
        <L2Card
          channel={{ ...channel, ...draft } as NotificationChannel}
          onChange={patch}
        />
        <L3Card
          channel={{ ...channel, ...draft } as NotificationChannel}
          onChange={patch}
        />
        <DeliveryCard
          channel={{ ...channel, ...draft } as NotificationChannel}
          onChange={patch}
        />
        <LivePreviewCard channel={channel} draft={draft} />
      </div>

      <div className="sticky bottom-0 mt-6 flex items-center justify-end gap-2 border-t bg-background/95 backdrop-blur px-4 py-3 -mx-4">
        {dirty && (
          <Button variant="ghost" onClick={reset}>
            Discard changes
          </Button>
        )}
        <Button disabled={!dirty || update.isPending} onClick={save}>
          {update.isPending ? "Saving..." : "Save changes"}
        </Button>
      </div>

      <ActivationLinkModal
        open={!!activationModal}
        onOpenChange={(open) => !open && setActivationModal(null)}
        url={activationModal?.activation_url ?? null}
        expiresAt={activationModal?.activation_expires_at ?? null}
        channelName={activationModal?.channel.name ?? "the recipient"}
      />
      <ActivationLinkModal
        open={!!reissueModal}
        onOpenChange={(open) => !open && setReissueModal(null)}
        url={reissueModal?.url ?? null}
        expiresAt={reissueModal?.expires ?? null}
        channelName={channel.name}
      />
    </>
  );
}
