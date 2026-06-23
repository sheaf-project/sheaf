import { useMemo } from "react";
import { useMembers, useUnarchiveMember } from "@/hooks/use-members";
import { ColorDot } from "@/components/color-dot";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function ArchivedMembersCard() {
  const { data: members } = useMembers();
  const unarchive = useUnarchiveMember();

  const archived = useMemo(
    () => (members ?? []).filter((m) => m.archived_at != null),
    [members],
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Archived members</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Archived members are hidden from the roster and from front/journal
          pickers, but stay visible in front history and existing entries.
          Restore one to bring it back into circulation.
        </p>
        {archived.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No archived members.
          </p>
        ) : (
          <div className="space-y-2">
            {archived.map((m) => (
              <div
                key={m.id}
                className="flex items-center justify-between gap-3 rounded-md border px-3 py-2"
              >
                <span className="flex min-w-0 items-center gap-2 text-sm">
                  <ColorDot color={m.color} />
                  <span className="truncate">{m.display_name || m.name}</span>
                  {m.display_name && (
                    <span className="truncate text-xs text-muted-foreground">
                      ({m.name})
                    </span>
                  )}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 shrink-0 text-xs"
                  onClick={() => unarchive.mutate(m.id)}
                  disabled={unarchive.isPending && unarchive.variables === m.id}
                >
                  {unarchive.isPending && unarchive.variables === m.id
                    ? "Unarchiving..."
                    : "Unarchive"}
                </Button>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
