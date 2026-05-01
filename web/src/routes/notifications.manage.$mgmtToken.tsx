import { useEffect, useState } from "react";
import { useParams } from "react-router";
import { Bell, Check, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Logo } from "@/components/logo";
import {
  unsubscribeManagedChannel,
  viewManagedChannel,
} from "@/lib/notification-redemption";
import type { ManageChannelView } from "@/types/api";

export function NotificationsManagePage() {
  const { mgmtToken } = useParams<{ mgmtToken: string }>();
  const [view, setView] = useState<ManageChannelView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unsubscribing, setUnsubscribing] = useState(false);

  useEffect(() => {
    if (!mgmtToken) return;
    let cancelled = false;
    viewManagedChannel(mgmtToken)
      .then((v) => {
        if (!cancelled) setView(v);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message ?? "Couldn't load subscription.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mgmtToken]);

  async function unsubscribe() {
    if (!mgmtToken) return;
    setUnsubscribing(true);
    try {
      await unsubscribeManagedChannel(mgmtToken);
      const fresh = await viewManagedChannel(mgmtToken);
      setView(fresh);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUnsubscribing(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-md space-y-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <Logo className="h-10 w-10 rounded-md" />
          <h1 className="text-2xl font-semibold">Manage subscription</h1>
        </div>

        {loading && (
          <Card>
            <CardContent className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading...
            </CardContent>
          </Card>
        )}

        {error && (
          <Card>
            <CardContent className="p-6 text-sm text-destructive">
              {error}
            </CardContent>
          </Card>
        )}

        {view && (
          <Card>
            <CardContent className="space-y-4 p-6">
              <div className="flex items-center gap-3">
                <Bell className="h-6 w-6 text-muted-foreground" />
                <div>
                  <p className="text-base font-medium">{view.channel_name}</p>
                  {view.system_label && (
                    <p className="text-xs text-muted-foreground">
                      from {view.system_label}
                    </p>
                  )}
                </div>
              </div>

              <div className="rounded border bg-muted/30 px-3 py-2 text-sm">
                Status:{" "}
                <strong className="capitalize">
                  {view.destination_state.replace("_", " ")}
                </strong>
              </div>

              {view.destination_state === "active" ? (
                <Button
                  variant="destructive"
                  className="w-full"
                  onClick={unsubscribe}
                  disabled={unsubscribing}
                >
                  {unsubscribing ? "Unsubscribing..." : "Unsubscribe"}
                </Button>
              ) : (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Check className="h-4 w-4 text-emerald-500" />
                  You will no longer receive notifications on this channel.
                </p>
              )}

              <p className="text-xs text-muted-foreground">
                Unsubscribing here doesn't notify the owner. They can see the
                channel is disabled, but won't be told when or by whom.
              </p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
