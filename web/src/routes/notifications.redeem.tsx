import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Bell, Check, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Logo } from "@/components/logo";
import { redeemActivation } from "@/lib/notification-redemption";

type Phase =
  | { kind: "idle" }
  | { kind: "requesting" }
  | { kind: "subscribing" }
  | { kind: "redeeming" }
  | {
      kind: "ok";
      channelName: string;
      systemLabel: string | null;
      managementUrl: string;
    }
  | { kind: "error"; message: string };

async function getOrCreatePushSubscription(): Promise<PushSubscription | null> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    throw new Error("This browser doesn't support web push.");
  }
  // Reuse an existing service worker if one is already registered, or
  // register a minimal one. The empty SW only handles `push` events well
  // enough to display notifications. Sheaf ships a tiny default at /sw.js.
  let reg: ServiceWorkerRegistration;
  try {
    reg = await navigator.serviceWorker.register("/sw.js");
  } catch {
    throw new Error("Couldn't register service worker.");
  }
  await navigator.serviceWorker.ready;

  const existing = await reg.pushManager.getSubscription();
  if (existing) return existing;

  // We need the server's VAPID public key to subscribe. The /v1/version
  // endpoint can expose it in future; for now we accept a 404 from the
  // unauth fetch and just request without applicationServerKey, which
  // most browsers reject. Show a clear error if so.
  let appKey: BufferSource | undefined;
  try {
    const resp = await fetch("/v1/version");
    if (resp.ok) {
      const data = await resp.json();
      if (data.vapid_public_key) {
        appKey = urlBase64ToUint8Array(data.vapid_public_key);
      }
    }
  } catch {
    // ignore; fall through
  }

  return reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: appKey,
  });
}

function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  // Allocate an ArrayBuffer explicitly: TS 5.7+ widens
  // `new Uint8Array(n)` to Uint8Array<ArrayBufferLike>, which includes
  // SharedArrayBuffer and so isn't assignable to BufferSource (what the
  // PushManager.subscribe applicationServerKey expects). Allocating the
  // buffer ourselves narrows the type to Uint8Array<ArrayBuffer>.
  const buf = new ArrayBuffer(raw.length);
  const out = new Uint8Array(buf);
  for (let i = 0; i < raw.length; ++i) out[i] = raw.charCodeAt(i);
  return out;
}

export function NotificationsRedeemPage() {
  const [params] = useSearchParams();
  const code = params.get("code") ?? "";
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  useEffect(() => {
    if (!code) {
      setPhase({ kind: "error", message: "Missing activation code." });
      return;
    }
  }, [code]);

  async function activate() {
    setPhase({ kind: "requesting" });
    try {
      if (!("Notification" in window)) {
        throw new Error("This browser doesn't support notifications.");
      }
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        throw new Error("Notification permission was denied.");
      }
      setPhase({ kind: "subscribing" });
      const sub = await getOrCreatePushSubscription();
      if (!sub) {
        throw new Error("Couldn't create push subscription.");
      }

      setPhase({ kind: "redeeming" });
      const json = sub.toJSON();
      const resp = await redeemActivation({
        activation_code: code,
        push_subscription: {
          endpoint: json.endpoint!,
          keys: (json.keys as Record<string, string>) ?? {},
        },
      });
      setPhase({
        kind: "ok",
        channelName: resp.channel_name,
        systemLabel: resp.system_label,
        managementUrl: resp.management_url,
      });
    } catch (exc) {
      setPhase({
        kind: "error",
        message: exc instanceof Error ? exc.message : String(exc),
      });
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-md space-y-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <Logo className="h-10 w-10 rounded-md" />
          <h1 className="text-2xl font-semibold">Subscribe to notifications</h1>
        </div>

        {phase.kind === "ok" ? (
          <Card>
            <CardContent className="space-y-4 p-6 text-center">
              <Check className="h-12 w-12 mx-auto text-emerald-500" />
              <p className="text-base font-medium">You're subscribed.</p>
              <p className="text-sm text-muted-foreground">
                {phase.systemLabel
                  ? `${phase.systemLabel}'s `
                  : ""}
                front-change notifications will now arrive in this browser
                under the channel <strong>{phase.channelName}</strong>.
              </p>
              <Button asChild variant="outline" className="w-full">
                <Link to={phase.managementUrl}>Manage subscription</Link>
              </Button>
            </CardContent>
          </Card>
        ) : phase.kind === "error" ? (
          <Card>
            <CardContent className="space-y-3 p-6 text-center">
              <p className="text-sm text-destructive">{phase.message}</p>
              <Button onClick={activate}>Try again</Button>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="space-y-4 p-6">
              <p className="text-sm text-muted-foreground">
                The owner of a Sheaf system has invited you to receive
                notifications when fronts change. You'll only receive what
                they've configured for you &mdash; no data access, just pings.
              </p>
              <Button onClick={activate} className="w-full" disabled={!code}>
                {phase.kind === "requesting" ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Requesting permission...
                  </>
                ) : phase.kind === "subscribing" ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Setting up subscription...
                  </>
                ) : phase.kind === "redeeming" ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Activating...
                  </>
                ) : (
                  <>
                    <Bell className="mr-2 h-4 w-4" />
                    Allow notifications
                  </>
                )}
              </Button>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
