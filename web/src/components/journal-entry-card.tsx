import { Link } from "react-router";
import { Card, CardContent } from "@/components/ui/card";
import { PendingDeleteBadge } from "@/components/pending-delete-badge";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { cn } from "@/lib/utils";
import type { JournalEntry, Member } from "@/types/api";

const SNIPPET_CHARS = 180;

function snippet(body: string): string {
  // Strip markdown image embeds for the list snippet — they look noisy.
  const stripped = body
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (stripped.length <= SNIPPET_CHARS) return stripped;
  return stripped.slice(0, SNIPPET_CHARS).trimEnd() + "…";
}

export function JournalEntryCard({
  entry,
  memberLookup,
}: {
  entry: JournalEntry;
  memberLookup?: Map<string, Member>;
}) {
  const { formatDateTime } = useDateFormatters();
  const member = entry.member_id ? memberLookup?.get(entry.member_id) : null;
  const titleDisplay =
    entry.title || `Entry from ${formatDateTime(entry.created_at)}`;
  const authors = entry.author_member_names.length > 0
    ? entry.author_member_names.join(", ")
    : "account";

  return (
    <Link to={`/journals/${entry.id}`} className="block">
      <Card
        className={cn(
          "cursor-pointer transition-colors hover:bg-accent/50",
          entry.pending_delete_at && "opacity-60",
        )}
      >
        <CardContent className="space-y-1 p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="font-medium truncate">{titleDisplay}</p>
            <span className="text-xs text-muted-foreground shrink-0">
              {formatDateTime(entry.created_at)}
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            {member ? (
              <>
                <span className="font-medium">
                  {member.display_name || member.name}
                </span>{" "}
                · written by {authors}
              </>
            ) : (
              <>System-wide · written by {authors}</>
            )}
          </p>
          <p className="text-sm text-muted-foreground line-clamp-2">
            {snippet(entry.body) || "(empty)"}
          </p>
          <PendingDeleteBadge finalizeAt={entry.pending_delete_at} />
        </CardContent>
      </Card>
    </Link>
  );
}
