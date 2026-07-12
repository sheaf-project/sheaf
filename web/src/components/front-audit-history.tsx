import { useQuery } from "@tanstack/react-query";

import { listFrontAudit } from "@/lib/fronts";
import { useMembers } from "@/hooks/use-members";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { Skeleton } from "@/components/ui/skeleton";
import type { FrontSnapshot } from "@/types/api";

function memberNames(
  ids: string[],
  members: Map<string, { display_name: string | null; name: string }>,
): string {
  if (ids.length === 0) return "(none)";
  return ids
    .map((id) => {
      const m = members.get(id);
      return m?.display_name ?? m?.name ?? "Unknown";
    })
    .join(", ");
}

function diff(
  before: FrontSnapshot,
  after: FrontSnapshot,
  members: Map<string, { display_name: string | null; name: string }>,
  formatDateTime: (d: string | null | undefined) => string,
): { label: string; from: string; to: string }[] {
  const rows: { label: string; from: string; to: string }[] = [];

  const beforeIds = [...before.member_ids].sort();
  const afterIds = [...after.member_ids].sort();
  if (
    beforeIds.length !== afterIds.length ||
    beforeIds.some((id, i) => id !== afterIds[i])
  ) {
    rows.push({
      label: "Members",
      from: memberNames(before.member_ids, members),
      to: memberNames(after.member_ids, members),
    });
  }
  if (before.started_at !== after.started_at) {
    rows.push({
      label: "Started",
      from: formatDateTime(before.started_at),
      to: formatDateTime(after.started_at),
    });
  }
  if (before.ended_at !== after.ended_at) {
    rows.push({
      label: "Ended",
      from: before.ended_at ? formatDateTime(before.ended_at) : "(open)",
      to: after.ended_at ? formatDateTime(after.ended_at) : "(open)",
    });
  }
  if ((before.custom_status ?? "") !== (after.custom_status ?? "")) {
    rows.push({
      label: "Status",
      from: before.custom_status || "(none)",
      to: after.custom_status || "(none)",
    });
  }
  return rows;
}

export function FrontAuditHistory({ frontId }: { frontId: string }) {
  const { formatDateTime } = useDateFormatters();
  const { data: events, isLoading } = useQuery({
    queryKey: ["fronts", frontId, "audit"],
    queryFn: () => listFrontAudit(frontId),
  });
  const { data: memberList } = useMembers();
  const members = new Map(
    memberList?.map((m) => [m.id, { display_name: m.display_name, name: m.name }]) ??
      [],
  );

  if (isLoading) {
    return <Skeleton className="h-12 w-full" />;
  }
  if (!events || events.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No edits recorded for this entry.
      </p>
    );
  }
  return (
    <ul className="space-y-3">
      {events.map((ev) => {
        const changes = diff(ev.before, ev.after, members, formatDateTime);
        return (
          <li
            key={ev.id}
            className="rounded-md border bg-muted/30 px-3 py-2 text-sm"
          >
            <div className="text-xs text-muted-foreground">
              {formatDateTime(ev.created_at)}
              {ev.fronting_member_ids.length > 0 && (
                <span>
                  {" "}· at-front: {memberNames(ev.fronting_member_ids, members)}
                </span>
              )}
            </div>
            {changes.length === 0 ? (
              <p className="text-xs italic text-muted-foreground">
                (no diff)
              </p>
            ) : (
              <ul className="mt-1 space-y-0.5">
                {changes.map((c) => (
                  <li key={c.label} className="text-xs">
                    <span className="font-medium">{c.label}:</span>{" "}
                    <span className="text-muted-foreground">{c.from}</span>
                    {" → "}
                    <span>{c.to}</span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}
