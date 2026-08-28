import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useMembers, useUnarchiveMember } from "@/hooks/use-members";
import { getMySystem } from "@/lib/systems";
import { getSystemSafety } from "@/lib/system-safety";
import { isStepUpRequiredError, showApiErrorToast } from "@/lib/api-errors";
import type { DeleteConfirmation, DestructiveConfirm, Member } from "@/types/api";
import { ColorDot } from "@/components/color-dot";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function ArchivedMembersCard() {
  const { data: members } = useMembers();
  const unarchive = useUnarchiveMember();
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const { data: safety } = useQuery({
    queryKey: ["system-safety"],
    queryFn: getSystemSafety,
  });
  // Restoring somebody who is still in a published view is an exposing act,
  // so the server asks for re-auth first. Same retry-on-refusal shape as the
  // members page: try bare, and only prompt once the server says it needs one.
  const [confirming, setConfirming] = useState<{
    member: Member;
    tier: DeleteConfirmation;
  } | null>(null);

  const archived = useMemo(
    () => (members ?? []).filter((m) => m.archived_at != null),
    [members],
  );

  function restore(member: Member, confirm?: DestructiveConfirm) {
    unarchive.mutate(
      { id: member.id, confirm, skipErrorToast: !confirm },
      {
        onSuccess: () => setConfirming(null),
        onError: (err) => {
          if (!confirm && isStepUpRequiredError(err)) {
            setConfirming({
              member,
              tier:
                safety?.settings.auth_tier ??
                system?.delete_confirmation ??
                "password",
            });
            return;
          }
          showApiErrorToast(err, "Couldn't unarchive member.", { force: true });
        },
      },
    );
  }

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
            {archived.map((m) => {
              const busy =
                unarchive.isPending && unarchive.variables?.id === m.id;
              return (
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
                    onClick={() => restore(m)}
                    disabled={busy}
                  >
                    {busy ? "Unarchiving..." : "Unarchive"}
                  </Button>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>

      <DestructiveConfirmDialog
        open={!!confirming}
        onOpenChange={(open) => !open && setConfirming(null)}
        title="Confirm restoring a shared member"
        description="This member is still in a view you publish, so restoring them puts them back on a public profile or share link. Confirm now; with a grace period set they return to those pages after your System Safety window, and to your own lists straight away."
        tier={confirming?.tier ?? "none"}
        actionLabel="Restore member"
        actionLabelLoading="Restoring..."
        loading={unarchive.isPending}
        onConfirm={(confirm?: DestructiveConfirm) =>
          confirming && restore(confirming.member, confirm ?? {})
        }
      />
    </Card>
  );
}
