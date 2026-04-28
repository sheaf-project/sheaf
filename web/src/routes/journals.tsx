import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { JournalEntryCard } from "@/components/journal-entry-card";
import { JournalEntryEditor } from "@/components/journal-entry-editor";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCurrentFronts } from "@/hooks/use-fronts";
import { useMembers } from "@/hooks/use-members";
import { createJournal, listJournals } from "@/lib/journals";
import { getMySystem } from "@/lib/systems";
import type { JournalListResponse, Member } from "@/types/api";

const PAGE_LIMIT = 25;

type Filter = "all" | "system" | string; // member id or sentinel

export function JournalsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialMember = searchParams.get("member_id");
  const initialScope = searchParams.get("scope");
  const [filter, setFilter] = useState<Filter>(
    initialMember ? initialMember : initialScope === "system" ? "system" : "all",
  );
  const [creating, setCreating] = useState(false);
  const [presetMemberId, setPresetMemberId] = useState<string | null>(null);
  const { data: members } = useMembers();

  useEffect(() => {
    if (filter === "all") {
      if (searchParams.has("member_id") || searchParams.has("scope")) {
        setSearchParams({}, { replace: true });
      }
    } else if (filter === "system") {
      setSearchParams({ scope: "system" }, { replace: true });
    } else {
      setSearchParams({ member_id: filter }, { replace: true });
    }
  }, [filter, searchParams, setSearchParams]);

  const memberLookup = useMemo(() => {
    const m = new Map<string, Member>();
    for (const member of members ?? []) m.set(member.id, member);
    return m;
  }, [members]);

  return (
    <>
      <PageHeader title="Journals">
        <Button
          onClick={() => {
            setPresetMemberId(null);
            setCreating(true);
          }}
        >
          <Plus className="h-4 w-4 mr-1" />
          New entry
        </Button>
      </PageHeader>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Tabs
          value={filter === "all" || filter === "system" ? filter : "member"}
          onValueChange={(v) => {
            if (v === "all" || v === "system") setFilter(v);
          }}
        >
          <TabsList>
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="system">System-wide</TabsTrigger>
            {members && members.length > 0 && (
              <TabsTrigger
                value="member"
                onClick={() => {
                  if (filter === "all" || filter === "system")
                    setFilter(members[0].id);
                }}
              >
                Member
              </TabsTrigger>
            )}
          </TabsList>
        </Tabs>
        {(filter !== "all" && filter !== "system") && members && (
          <select
            className="rounded-md border bg-background px-2 py-1 text-sm"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name || m.name}
              </option>
            ))}
          </select>
        )}
      </div>

      <JournalList
        filter={filter}
        memberLookup={memberLookup}
        members={members ?? []}
      />

      {creating && (
        <CreateEntryDialog
          presetMemberId={presetMemberId}
          members={members ?? []}
          defaultMemberId={
            filter !== "all" && filter !== "system" ? filter : null
          }
          onClose={() => setCreating(false)}
        />
      )}
    </>
  );
}

function JournalList({
  filter,
  memberLookup,
  members,
}: {
  filter: Filter;
  memberLookup: Map<string, Member>;
  members: Member[];
}) {
  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
  });
  const dateFormat = system?.date_format ?? "ymd";

  const params = useMemo(() => {
    if (filter === "system") return { system_only: true, limit: PAGE_LIMIT };
    if (filter !== "all") return { member_id: filter, limit: PAGE_LIMIT };
    return { limit: PAGE_LIMIT };
  }, [filter]);

  const query = useInfiniteQuery<JournalListResponse>({
    queryKey: ["journals", params],
    queryFn: ({ pageParam }) =>
      listJournals({ ...params, before: pageParam as string | undefined }),
    initialPageParam: undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

  if (query.isLoading) {
    return (
      <div className="grid gap-3">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
    );
  }

  const entries = query.data?.pages.flatMap((p) => p.items) ?? [];

  if (entries.length === 0) {
    return (
      <p className="text-muted-foreground">
        No entries yet.{" "}
        {members.length === 0 && filter !== "system"
          ? "Create a member or switch to system-wide entries."
          : "Click \"New entry\" to write the first one."}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3">
        {entries.map((entry) => (
          <JournalEntryCard
            key={entry.id}
            entry={entry}
            memberLookup={memberLookup}
            dateFormat={dateFormat}
          />
        ))}
      </div>
      {query.hasNextPage && (
        <div className="flex justify-center">
          <Button
            variant="outline"
            onClick={() => query.fetchNextPage()}
            disabled={query.isFetchingNextPage}
          >
            {query.isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        </div>
      )}
    </div>
  );
}

function CreateEntryDialog({
  presetMemberId,
  defaultMemberId,
  members,
  onClose,
}: {
  presetMemberId: string | null;
  defaultMemberId: string | null;
  members: Member[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [memberId, setMemberId] = useState<string | null>(
    presetMemberId ?? defaultMemberId,
  );
  const { data: currentFronts } = useCurrentFronts();
  const defaultAuthors = useMemo(() => {
    if (!currentFronts) return [];
    const ids = new Set<string>();
    for (const f of currentFronts) {
      for (const mid of f.member_ids) ids.add(mid);
    }
    return Array.from(ids);
  }, [currentFronts]);

  const create = useMutation({
    mutationFn: createJournal,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["journals"] });
      toast.success("Entry created");
      onClose();
    },
  });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New journal entry</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <label className="text-sm">Scope</label>
            <select
              className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              value={memberId ?? ""}
              onChange={(e) => setMemberId(e.target.value || null)}
            >
              <option value="">System-wide</option>
              {members.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name || m.name}
                </option>
              ))}
            </select>
          </div>
          <JournalEntryEditor
            initial={{ authorMemberIds: defaultAuthors }}
            members={members}
            saving={create.isPending}
            onSubmit={({ title, body, authorMemberIds }) =>
              create.mutate({
                member_id: memberId,
                title: title || null,
                body,
                author_member_ids: authorMemberIds,
              })
            }
            onCancel={onClose}
            submitLabel="Create"
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
