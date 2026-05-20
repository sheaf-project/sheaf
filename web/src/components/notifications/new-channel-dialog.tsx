import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCreateChannel } from "@/hooks/use-notifications";
import { getNotificationsServerConfig } from "@/lib/notifications";
import type { ChannelCreate, ChannelCreateResponse, DestinationType } from "@/types/api";

export function NewChannelDialog({
  open,
  onOpenChange,
  tokenId,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tokenId: string;
  onCreated?: (resp: ChannelCreateResponse) => void;
}) {
  const create = useCreateChannel(tokenId);
  const { data: serverCfg } = useQuery({
    queryKey: ["notifications", "server-config"],
    queryFn: getNotificationsServerConfig,
  });
  const minDebounce =
    serverCfg?.pushover.shared_app_min_debounce_seconds ?? 0;
  const sharedAppAvailable = serverCfg?.pushover.shared_app_available ?? true;

  const [name, setName] = useState("");
  const [type, setType] = useState<DestinationType>("web_push");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [webhookFormat, setWebhookFormat] = useState<
    "json" | "discord" | "slack" | "plaintext"
  >("json");
  const [ntfyServer, setNtfyServer] = useState("https://ntfy.sh");
  const [ntfyTopic, setNtfyTopic] = useState("");
  const [pushoverUserKey, setPushoverUserKey] = useState("");
  const [pushoverAppToken, setPushoverAppToken] = useState("");
  const [pushoverAdvanced, setPushoverAdvanced] = useState(false);

  function reset() {
    setName("");
    setType("web_push");
    setWebhookUrl("");
    setWebhookSecret("");
    setWebhookFormat("json");
    setNtfyServer("https://ntfy.sh");
    setNtfyTopic("");
    setPushoverUserKey("");
    setPushoverAppToken("");
    setPushoverAdvanced(false);
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const data: ChannelCreate = {
      name,
      destination_type: type,
      base_all_members: true,
      trigger_on_start: true,
    };
    // For shared-app Pushover channels, start at the operator's floor so
    // the user doesn't get rejected on create with the schema default of
    // 30s. BYO channels skip this — they're on the recipient's own quota.
    if (type === "pushover" && !pushoverAppToken.trim() && minDebounce > 0) {
      data.debounce_seconds = minDebounce;
    }
    if (type === "webhook") {
      data.destination_config = { url: webhookUrl, format: webhookFormat };
      // HMAC only meaningful for json/plaintext; the discord/slack endpoints
      // don't validate signatures so we don't bother storing a secret.
      if (
        webhookSecret &&
        (webhookFormat === "json" || webhookFormat === "plaintext")
      ) {
        data.webhook_secret = webhookSecret;
      }
    } else if (type === "ntfy") {
      data.destination_config = { server_url: ntfyServer, topic: ntfyTopic };
    } else if (type === "pushover") {
      const cfg: Record<string, string> = { user_key: pushoverUserKey };
      if (pushoverAppToken.trim()) {
        cfg.app_token = pushoverAppToken.trim();
      }
      data.destination_config = cfg;
    }

    create.mutate(data, {
      onSuccess: (resp) => {
        onCreated?.(resp);
        reset();
        onOpenChange(false);
      },
    });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New channel</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="new-channel-name">Name</Label>
            <Input
              id="new-channel-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Mara's phone"
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-channel-destination">Destination</Label>
            <Select value={type} onValueChange={(v) => setType(v as DestinationType)}>
              <SelectTrigger id="new-channel-destination">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="web_push">Web push (browser)</SelectItem>
                <SelectItem value="mobile_push">Mobile push (iOS + Android)</SelectItem>
                <SelectItem value="webhook">Webhook</SelectItem>
                <SelectItem value="ntfy">ntfy</SelectItem>
                <SelectItem value="pushover">Pushover</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {type === "webhook" && (
            <>
              <div className="space-y-2">
                <Label htmlFor="webhook-url">URL</Label>
                <Input
                  id="webhook-url"
                  type="url"
                  value={webhookUrl}
                  onChange={(e) => setWebhookUrl(e.target.value)}
                  placeholder="https://example.com/webhook"
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="webhook-format">Payload format</Label>
                <Select
                  value={webhookFormat}
                  onValueChange={(v) =>
                    setWebhookFormat(v as typeof webhookFormat)
                  }
                >
                  <SelectTrigger id="webhook-format">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="json">
                      JSON (Sheaf format, signed)
                    </SelectItem>
                    <SelectItem value="discord">Discord</SelectItem>
                    <SelectItem value="slack">Slack-compatible</SelectItem>
                    <SelectItem value="plaintext">Plain text</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  {webhookFormat === "json" &&
                    "POST application/json with event_id/title/body. Add a secret to sign with HMAC."}
                  {webhookFormat === "discord" &&
                    "POST {content: \"...\"} matching Discord's incoming-webhook schema. No signature."}
                  {webhookFormat === "slack" &&
                    "POST {text: \"...\"} matching Slack's incoming-webhook schema. Works for many chat tools."}
                  {webhookFormat === "plaintext" &&
                    "POST text/plain body of \"title\\nbody\". Useful for SMS gateways and ad-hoc collectors."}
                </p>
              </div>
              {(webhookFormat === "json" || webhookFormat === "plaintext") && (
                <div className="space-y-2">
                  <Label htmlFor="webhook-secret">Secret (optional)</Label>
                  <Input
                    id="webhook-secret"
                    type="password"
                    value={webhookSecret}
                    onChange={(e) => setWebhookSecret(e.target.value)}
                    placeholder="Used for HMAC signature header"
                  />
                </div>
              )}
            </>
          )}

          {type === "ntfy" && (
            <>
              <div className="space-y-2">
                <Label htmlFor="ntfy-server">Server URL</Label>
                <Input
                  id="ntfy-server"
                  type="url"
                  value={ntfyServer}
                  onChange={(e) => setNtfyServer(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="ntfy-topic">Topic</Label>
                <Input
                  id="ntfy-topic"
                  value={ntfyTopic}
                  onChange={(e) => setNtfyTopic(e.target.value)}
                  required
                />
              </div>
            </>
          )}

          {type === "pushover" && (
            <>
              <div className="space-y-2">
                <Label htmlFor="pushover-user-key">User key</Label>
                <Input
                  id="pushover-user-key"
                  value={pushoverUserKey}
                  onChange={(e) => setPushoverUserKey(e.target.value)}
                  placeholder="From your Pushover dashboard"
                  required
                />
              </div>
              {!sharedAppAvailable && !pushoverAppToken && (
                <p className="text-xs text-destructive">
                  This instance has no shared Pushover app configured —
                  you'll need to bring your own app token below.
                </p>
              )}
              <div className="space-y-2 rounded-md border bg-muted/30 px-3 py-2">
                <button
                  type="button"
                  onClick={() => setPushoverAdvanced((v) => !v)}
                  className="flex w-full items-center justify-between text-left text-sm font-medium"
                >
                  <span>Advanced: bring your own Pushover app</span>
                  <span className="text-xs text-muted-foreground">
                    {pushoverAdvanced ? "−" : "+"}
                  </span>
                </button>
                {pushoverAdvanced && (
                  <>
                    <p className="text-xs text-muted-foreground">
                      Pushover gives each account a 10,000 messages/month
                      free quota across all the apps it owns. This instance
                      shares one app across all recipients, so it caps
                      total monthly traffic, applies per-user allowances by
                      tier, and enforces a longer minimum debounce
                      {minDebounce > 0
                        ? ` (${Math.round(minDebounce / 60)} min)`
                        : ""}
                      . Create your own free Pushover application at{" "}
                      <a
                        href="https://pushover.net/apps/build"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="underline"
                      >
                        pushover.net/apps/build
                      </a>{" "}
                      and paste its API token below to bypass all of those
                      — you'll hit your own account's 10k/month free pool
                      instead.
                    </p>
                    <Label htmlFor="pushover-app-token" className="text-xs">App token (optional)</Label>
                    <Input
                      id="pushover-app-token"
                      value={pushoverAppToken}
                      onChange={(e) => setPushoverAppToken(e.target.value)}
                      placeholder="a-30-char-pushover-app-token"
                    />
                  </>
                )}
              </div>
            </>
          )}

          {type === "web_push" && (
            <p className="text-sm text-muted-foreground">
              You'll get a one-time activation link to send to the recipient.
              They open it in their browser, grant push permission, and the
              channel becomes active.
            </p>
          )}

          {type === "mobile_push" && (
            <p className="text-sm text-muted-foreground">
              You'll get a one-time activation link to send to the recipient.
              They open it on their phone (with the Sheaf app installed and
              signed in to their account), the app handles redemption, and
              the channel becomes active. The link works on both iOS and
              Android — one channel rings every device the recipient has
              signed into.
            </p>
          )}

          <DialogFooter>
            <Button type="submit" disabled={create.isPending || !name}>
              {create.isPending ? "Creating..." : "Create channel"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
