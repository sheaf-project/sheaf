import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";

import { PageHeader } from "@/components/page-header";
import { ReceivingList } from "@/components/notifications/receiving-list";
import { WatchTokenCard } from "@/components/notifications/watch-token-card";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useCreateWatchToken,
  useWatchTokens,
} from "@/hooks/use-notifications";
import { getMyPushoverUsage } from "@/lib/notifications";
import { getMySystem } from "@/lib/systems";

export function NotificationsPage() {
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const { data: tokens, isLoading } = useWatchTokens(system?.id);
  const { data: pushoverUsage } = useQuery({
    queryKey: ["notifications", "my-pushover-usage"],
    queryFn: getMyPushoverUsage,
  });
  const createToken = useCreateWatchToken(system?.id);
  const [showNew, setShowNew] = useState(false);
  const [label, setLabel] = useState("");
  const [tab, setTab] = useState<"sending" | "receiving">("sending");

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    createToken.mutate(
      { label: label || null },
      {
        onSuccess: () => {
          setShowNew(false);
          setLabel("");
        },
      },
    );
  }

  return (
    <>
      <PageHeader title="Notifications">
        {tab === "sending" && (
          <Button onClick={() => setShowNew(true)}>Add watcher</Button>
        )}
      </PageHeader>

      <Tabs value={tab} onValueChange={(v) => setTab(v as "sending" | "receiving")}>
        <TabsList>
          <TabsTrigger value="sending">Sending</TabsTrigger>
          <TabsTrigger value="receiving">Receiving</TabsTrigger>
        </TabsList>

        <TabsContent value="sending" className="mt-4">
          <p className="text-sm text-muted-foreground mb-4 max-w-prose">
            Watchers let trusted people get pinged when fronts change. Each
            watcher can have multiple channels (push, webhook, ntfy, Pushover)
            and each channel has its own filters &mdash; you control what each
            recipient is allowed to see, per-member.
          </p>

          {pushoverUsage && pushoverUsage.enforced && (
            <div className="mb-4 max-w-prose rounded-md border bg-muted/30 px-3 py-2 text-xs">
              <p>
                <strong>Pushover (shared app):</strong>{" "}
                <span
                  className={
                    pushoverUsage.count >= pushoverUsage.cap
                      ? "text-destructive"
                      : ""
                  }
                >
                  {pushoverUsage.count.toLocaleString()} /{" "}
                  {pushoverUsage.cap.toLocaleString()}
                </span>{" "}
                deliveries this month ({pushoverUsage.month}, {pushoverUsage.tier} tier).
                {pushoverUsage.count >= pushoverUsage.cap && (
                  <>
                    {" "}
                    Cap reached &mdash; further Pushover deliveries are paused
                    until next month, or until you paste your own Pushover app
                    token in a channel's Advanced config.
                  </>
                )}
              </p>
            </div>
          )}

          {isLoading ? (
            <div className="space-y-3">
              {[1, 2].map((i) => (
                <Skeleton key={i} className="h-32" />
              ))}
            </div>
          ) : tokens && tokens.length > 0 && system ? (
            <div className="space-y-3">
              {tokens.map((t) => (
                <WatchTokenCard key={t.id} token={t} systemId={system.id} />
              ))}
            </div>
          ) : (
            <p className="text-muted-foreground">
              No watchers yet. Add one to start sharing front-change pings.
            </p>
          )}
        </TabsContent>

        <TabsContent value="receiving" className="mt-4">
          <p className="text-sm text-muted-foreground mb-4 max-w-prose">
            Notifications you receive from other systems. Channels show up
            here when you redeem an activation link while signed in to your
            account &mdash; the owner sees you as a recipient and you can
            manage everything from one place.
          </p>
          <ReceivingList />
        </TabsContent>
      </Tabs>

      <Dialog open={showNew} onOpenChange={setShowNew}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New watcher</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleCreate} className="space-y-4">
            <div className="space-y-2">
              <Label>Label (optional)</Label>
              <Input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="e.g. Mara, my therapist, Discord bot"
                autoFocus
              />
              <p className="text-xs text-muted-foreground">
                For your reference only &mdash; recipients never see this.
              </p>
            </div>
            <DialogFooter>
              <Button type="submit" disabled={createToken.isPending}>
                {createToken.isPending ? "Creating..." : "Create"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
