import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Bell, Check, Loader2, Smartphone } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Logo } from "@/components/logo";
import {
  previewActivation,
  redeemActivation,
} from "@/lib/notification-redemption";
import type { DestinationType, RedeemPreview } from "@/types/api";

type Phase =
  | { kind: "loading-preview" }
  | { kind: "preview-loaded"; preview: RedeemPreview }
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

const MOBILE_DESTINATION_TYPES = new Set<DestinationType>([
  "mobile_push",
  // Legacy types kept for read-back of any pre-migration link.
  "fcm",
  "apns_dev",
  "apns_prod",
]);

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

  // Pull the deployment's VAPID public key from /v1/version. Browsers
  // reject subscribe() without applicationServerKey.
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

function buildDeepLink(code: string, channel: string | null): string {
  // The native apps register an intent filter for sheaf://notifications/redeem
  // and read `code` from the query. `channel` is informational; we forward
  // it verbatim if present.
  const params = new URLSearchParams({ code });
  if (channel) params.set("channel", channel);
  return `sheaf://notifications/redeem?${params.toString()}`;
}

export function NotificationsRedeemPage() {
  const [params] = useSearchParams();
  const code = params.get("code") ?? "";
  const channel = params.get("channel");
  const [phase, setPhase] = useState<Phase>({ kind: "loading-preview" });

  useEffect(() => {
    if (!code) {
      setPhase({ kind: "error", message: "Missing activation code." });
      return;
    }
    let cancelled = false;
    previewActivation(code)
      .then((preview) => {
        if (cancelled) return;
        setPhase({ kind: "preview-loaded", preview });
      })
      .catch((exc: unknown) => {
        if (cancelled) return;
        setPhase({
          kind: "error",
          message:
            exc instanceof Error ? exc.message : "Couldn't read activation link.",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [code]);

  async function activateWebPush() {
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

  function openInNativeApp() {
    // Just navigate the page to the deep link. If the app is installed
    // and registered for the URI, the OS hands off; otherwise the
    // browser sits there with no handler, which is fine — the recipient
    // is told above that they need the app installed first.
    window.location.href = buildDeepLink(code, channel);
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
                {phase.systemLabel ? `${phase.systemLabel}'s ` : ""}
                front-change notifications will now arrive in this browser
                under the channel <strong>{phase.channelName}</strong>.
              </p>
              {phase.managementUrl && (
                <Button asChild variant="outline" className="w-full">
                  <Link to={phase.managementUrl}>Manage subscription</Link>
                </Button>
              )}
            </CardContent>
          </Card>
        ) : phase.kind === "error" ? (
          <Card>
            <CardContent className="space-y-3 p-6 text-center">
              <p className="text-sm text-destructive">{phase.message}</p>
              {code && (
                <Button
                  onClick={() => {
                    setPhase({ kind: "loading-preview" });
                    previewActivation(code)
                      .then((preview) =>
                        setPhase({ kind: "preview-loaded", preview }),
                      )
                      .catch((exc: unknown) =>
                        setPhase({
                          kind: "error",
                          message:
                            exc instanceof Error ? exc.message : String(exc),
                        }),
                      );
                  }}
                >
                  Try again
                </Button>
              )}
            </CardContent>
          </Card>
        ) : phase.kind === "loading-preview" ? (
          <Card>
            <CardContent className="flex items-center justify-center p-8">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </CardContent>
          </Card>
        ) : phase.kind === "preview-loaded" &&
          MOBILE_DESTINATION_TYPES.has(phase.preview.destination_type) ? (
          <Card>
            <CardContent className="space-y-4 p-6">
              <p className="text-sm text-muted-foreground">
                <strong>{phase.preview.system_label ?? "A Sheaf system"}</strong>
                {" "}has invited you to receive front-change pings on the
                channel <strong>{phase.preview.channel_name}</strong>. This
                channel delivers via the native Sheaf app.
              </p>
              <Button onClick={openInNativeApp} className="w-full">
                <Smartphone className="mr-2 h-4 w-4" />
                Open in Sheaf
              </Button>
              <p className="text-xs text-muted-foreground text-center">
                Don't have the app installed? Install Sheaf first and then
                reopen this link from your inbox or messages.
              </p>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="space-y-4 p-6">
              {phase.kind === "preview-loaded" ? (
                <p className="text-sm text-muted-foreground">
                  <strong>
                    {phase.preview.system_label ?? "A Sheaf system"}
                  </strong>
                  {" "}has invited you to receive front-change pings on the
                  channel <strong>{phase.preview.channel_name}</strong>.
                  You'll only receive what they've configured for you &mdash;
                  no data access, just pings.
                </p>
              ) : (
                <p className="text-sm text-muted-foreground">
                  The owner of a Sheaf system has invited you to receive
                  notifications when fronts change. You'll only receive what
                  they've configured for you &mdash; no data access, just
                  pings.
                </p>
              )}
              <Button
                onClick={activateWebPush}
                className="w-full"
                disabled={!code || phase.kind === "requesting"}
              >
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
