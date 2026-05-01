import { useState, type FormEvent } from "react";

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

  function reset() {
    setName("");
    setType("web_push");
    setWebhookUrl("");
    setWebhookSecret("");
    setWebhookFormat("json");
    setNtfyServer("https://ntfy.sh");
    setNtfyTopic("");
    setPushoverUserKey("");
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const data: ChannelCreate = {
      name,
      destination_type: type,
      base_all_members: true,
      trigger_on_start: true,
    };
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
      data.destination_config = { user_key: pushoverUserKey };
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
            <Label>Name</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Mara's phone"
              required
            />
          </div>
          <div className="space-y-2">
            <Label>Destination</Label>
            <Select value={type} onValueChange={(v) => setType(v as DestinationType)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="web_push">Web push (browser)</SelectItem>
                <SelectItem value="webhook">Webhook</SelectItem>
                <SelectItem value="ntfy">ntfy</SelectItem>
                <SelectItem value="pushover">Pushover</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {type === "webhook" && (
            <>
              <div className="space-y-2">
                <Label>URL</Label>
                <Input
                  type="url"
                  value={webhookUrl}
                  onChange={(e) => setWebhookUrl(e.target.value)}
                  placeholder="https://example.com/webhook"
                  required
                />
              </div>
              <div className="space-y-2">
                <Label>Payload format</Label>
                <Select
                  value={webhookFormat}
                  onValueChange={(v) =>
                    setWebhookFormat(v as typeof webhookFormat)
                  }
                >
                  <SelectTrigger>
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
                  <Label>Secret (optional)</Label>
                  <Input
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
                <Label>Server URL</Label>
                <Input
                  type="url"
                  value={ntfyServer}
                  onChange={(e) => setNtfyServer(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label>Topic</Label>
                <Input
                  value={ntfyTopic}
                  onChange={(e) => setNtfyTopic(e.target.value)}
                  required
                />
              </div>
            </>
          )}

          {type === "pushover" && (
            <div className="space-y-2">
              <Label>User key</Label>
              <Input
                value={pushoverUserKey}
                onChange={(e) => setPushoverUserKey(e.target.value)}
                placeholder="From your Pushover dashboard"
                required
              />
            </div>
          )}

          {type === "web_push" && (
            <p className="text-sm text-muted-foreground">
              You'll get a one-time activation link to send to the recipient.
              They open it in their browser, grant push permission, and the
              channel becomes active.
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
