import { useEffect } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useChannelPreview } from "@/hooks/use-notifications";
import type { ChannelUpdate, NotificationChannel } from "@/types/api";

export function LivePreviewCard({
  channel,
  draft,
}: {
  channel: NotificationChannel;
  draft: ChannelUpdate;
}) {
  const preview = useChannelPreview(channel.id);
  const draftKey = JSON.stringify(draft);

  useEffect(() => {
    const t = window.setTimeout(() => {
      preview.mutate(draft);
    }, 250);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftKey, channel.id]);

  const data = preview.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Resolved members</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {!data && preview.isPending && (
          <p className="text-sm text-muted-foreground">Resolving...</p>
        )}
        {data && (
          <>
            {data.warnings.length > 0 && (
              <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm">
                {data.warnings.map((w, i) => (
                  <p key={i}>{w}</p>
                ))}
              </div>
            )}
            <div className="grid gap-4 md:grid-cols-2">
              <Column
                title={`Will receive (${data.included.length})`}
                tone="text-emerald-700 dark:text-emerald-400"
                rows={data.included}
              />
              <Column
                title={`Will not receive (${data.excluded.length})`}
                tone="text-muted-foreground"
                rows={data.excluded}
              />
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function Column({
  title,
  tone,
  rows,
}: {
  title: string;
  tone: string;
  rows: { member_id: string; name: string; is_private: boolean; attribution: string }[];
}) {
  return (
    <div className="space-y-2">
      <p className={`text-sm font-medium ${tone}`}>{title}</p>
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">None.</p>
      ) : (
        <ul className="max-h-72 space-y-1 overflow-y-auto pr-1">
          {rows.map((r) => (
            <li
              key={r.member_id}
              className="flex items-center justify-between rounded bg-muted/40 px-2 py-1 text-sm"
            >
              <span className="truncate">
                {r.name}
                {r.is_private && (
                  <span className="ml-1 text-xs text-muted-foreground">
                    (private)
                  </span>
                )}
              </span>
              <span className="text-xs text-muted-foreground ml-2 shrink-0">
                {r.attribution}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
