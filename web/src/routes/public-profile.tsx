import {
  createContext,
  lazy,
  Suspense,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { Link, useNavigate, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowLeftRight,
  ArrowRight,
  ChevronDown,
  ChevronRight,
  Loader2,
  Search,
  Users,
} from "lucide-react";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ColorDot } from "@/components/color-dot";
import { Logo } from "@/components/logo";
import { RelationshipGraphCanvas } from "@/components/relationship-graph";
import { ApiError } from "@/lib/api-error";
import { getAuthConfig } from "@/lib/auth";
import { isPublicImageAllowed } from "@/lib/image-sources";
import {
  isDirectedEdge,
  type GraphEdge,
  type GraphNode,
} from "@/lib/relationship-graph";
import { cn } from "@/lib/utils";
import {
  getPublicFronting,
  getPublicGroups,
  getPublicMember,
  getPublicMembers,
  getPublicRelationships,
  getPublicSystem,
} from "@/lib/public-profiles";
import type {
  PublicFrontingView,
  PublicGroupView,
  PublicGroupsView,
  PublicMemberView,
  PublicRelationship,
  PublicRelationshipsView,
  PublicSystemView,
} from "@/types/api";

const MarkdownPreview = lazy(() =>
  import("@/components/bio-editor").then((m) => ({ default: m.MarkdownPreview })),
);

/** Past this many shared members, a flat stack of full cards stops being
 *  something a visitor can actually read: it is several screens of scrolling
 *  before the first useful thing. Above the threshold the members tab leads
 *  with whoever is fronting and puts the rest behind a searchable, collapsed
 *  list; at or below it, the plain list is friendlier and is left alone. */
const LARGE_SYSTEM_MEMBERS = 25;

type Source =
  | { kind: "system"; systemId: string }
  | { kind: "link"; token: string };

/** Set robots=noindex for the lifetime of a public-profile page. The server
 *  also sends X-Robots-Tag, but a SPA route needs the meta tag too: link
 *  sharing, not search presence. */
function useNoIndex() {
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex, nofollow";
    document.head.appendChild(meta);
    return () => {
      document.head.removeChild(meta);
    };
  }, []);
}

function sourceKey(src: Source): string {
  return src.kind === "system" ? `system:${src.systemId}` : `link:${src.token}`;
}

function profilePath(src: Source): string {
  return src.kind === "system"
    ? `/p/${src.systemId}`
    : `/s/${encodeURIComponent(src.token)}`;
}

function memberPath(src: Source, memberId: string): string {
  return `${profilePath(src)}/member/${encodeURIComponent(memberId)}`;
}

/**
 * How a member's name, chip or card behaves when clicked.
 *
 * Two shapes, decided by the view: with member permalinks on, every member has
 * an address of their own and we link to it; with them off, the card opens in
 * place with no URL change, because there is no address to send anyone to.
 * Either way the target has to be a member this page is already holding a card
 * for - fronting is served independently of the roster, so a chip can name
 * somebody whose full card was never sent, and that one is not clickable.
 */
type MemberNav = {
  linkTo: ((id: string) => string) | null;
  open: (id: string) => void;
  card: (id: string) => PublicMemberView | undefined;
};

const MemberNavContext = createContext<MemberNav | null>(null);

/** Wraps a member's name/avatar in whatever opening them means here: a link, a
 *  button, or nothing at all. Renders phrasing content only, so it is safe
 *  inside a chip or a row. */
function MemberOpen({
  id,
  className,
  children,
}: {
  id: string;
  className?: string;
  children: React.ReactNode;
}) {
  const nav = useContext(MemberNavContext);
  const known = Boolean(nav?.card(id));
  if (!nav || !known) {
    return <span className={className}>{children}</span>;
  }
  const interactive = "cursor-pointer text-left transition-colors hover:bg-muted/60";
  if (nav.linkTo) {
    return (
      <Link to={nav.linkTo(id)} className={cn(className, interactive)}>
        {children}
      </Link>
    );
  }
  return (
    <button type="button" onClick={() => nav.open(id)} className={cn(className, interactive)}>
      {children}
    </button>
  );
}

export function PublicProfileView({ source }: { source: Source }) {
  useNoIndex();

  // Which member's card is open in place. Only ever used when the view has no
  // permalinks; with them on, opening a member is a navigation.
  const [openMemberId, setOpenMemberId] = useState<string | null>(null);
  const [tab, setTab] = useState<string | null>(null);

  const system = useQuery<PublicSystemView>({
    queryKey: ["public", sourceKey(source), "system"],
    queryFn: () => getPublicSystem(source),
    retry: false,
  });

  // The roster is opt-in per view like every other section: a 404 means the
  // view doesn't serve it, so the tab is absent rather than empty. Null = not
  // shared; an empty array means shared but nobody in it.
  const members = useQuery<PublicMemberView[] | null>({
    queryKey: ["public", sourceKey(source), "members"],
    queryFn: async () => {
      try {
        return await getPublicMembers(source);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    retry: false,
    enabled: system.isSuccess,
  });

  // Fronting is opt-in per view: a 404 means the view doesn't share it, so we
  // hide the section rather than showing an error. Null = not shared.
  const fronting = useQuery<PublicFrontingView | null>({
    queryKey: ["public", sourceKey(source), "fronting"],
    queryFn: async () => {
      try {
        return await getPublicFronting(source);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    retry: false,
    enabled: system.isSuccess,
    // Who is fronting changes on its own, so the page would otherwise show
    // whatever was true when it was opened. Matched to the 60s Cache-Control
    // on the public surface: polling faster would only re-read the cache.
    // Left to stop while the tab is hidden (no refetchIntervalInBackground).
    refetchInterval: 60_000,
  });

  // Opt-in per view like fronting, and a 404 means the same thing: not shared,
  // so the tab is absent rather than empty. No refetchInterval on purpose -
  // who a member is related to changes about never, so polling it would only
  // re-read the same cached response.
  const relationships = useQuery<PublicRelationshipsView | null>({
    queryKey: ["public", sourceKey(source), "relationships"],
    queryFn: async () => {
      try {
        return await getPublicRelationships(source);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    retry: false,
    enabled: system.isSuccess,
  });

  // Same again for groups: absent when the view doesn't publish them.
  const groups = useQuery<PublicGroupsView | null>({
    queryKey: ["public", sourceKey(source), "groups"],
    queryFn: async () => {
      try {
        return await getPublicGroups(source);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    retry: false,
    enabled: system.isSuccess,
  });

  const memberList = members.data ?? null;
  const permalinks = system.data?.member_permalinks ?? false;
  const byId = useMemo(
    () => new Map((memberList ?? []).map((m) => [m.id, m])),
    [memberList],
  );
  const nav = useMemo<MemberNav>(
    () => ({
      linkTo: permalinks ? (id: string) => memberPath(source, id) : null,
      open: (id: string) => setOpenMemberId(id),
      card: (id: string) => byId.get(id),
    }),
    [permalinks, byId, source],
  );

  if (system.isLoading) {
    return (
      <Centered>
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </Centered>
    );
  }

  if (system.isError || !system.data) {
    return <NotAvailable />;
  }

  const sys = system.data;
  const accent = sys.color ?? undefined;

  const tabs: { value: string; label: string }[] = [];
  if (memberList) tabs.push({ value: "members", label: "Members" });
  if (groups.data) tabs.push({ value: "groups", label: "Groups" });
  if (relationships.data) {
    tabs.push({ value: "relationships", label: "Relationships" });
  }
  // Controlled rather than defaultValue: the sections arrive one query at a
  // time, so the first available tab isn't known at mount. Falling back keeps
  // a chosen tab until it genuinely goes away.
  const activeTab = tabs.find((t) => t.value === tab)?.value ?? tabs[0]?.value;
  const openMember = openMemberId ? byId.get(openMemberId) : undefined;

  return (
    <MemberNavContext.Provider value={nav}>
      <div className="min-h-screen bg-muted/20">
        {/* Accent strip in the system's colour, for identity. */}
        <div className="h-2 w-full" style={accent ? { backgroundColor: accent } : undefined} />
        <div className="mx-auto w-full max-w-2xl px-4 py-8 space-y-6">
          <div className="text-center text-xs text-muted-foreground">
            {source.kind === "link"
              ? "You're viewing a shared profile."
              : "You're viewing a public profile."}
          </div>

          <div className="flex flex-col items-center gap-3 text-center">
            <Avatar className="size-20">
              {sys.avatar_url && isPublicImageAllowed(sys.avatar_url) && (
                <AvatarImage src={sys.avatar_url} />
              )}
              <AvatarFallback
                className="text-2xl"
                style={accent ? { backgroundColor: accent, color: "#fff" } : undefined}
              >
                {sys.name.charAt(0).toUpperCase()}
              </AvatarFallback>
            </Avatar>
            <div>
              <h1 className="text-2xl font-semibold">
                {sys.name}
                {sys.tag && (
                  <span className="ml-2 align-middle text-sm text-muted-foreground">
                    {sys.tag}
                  </span>
                )}
              </h1>
              {/* Null when the view doesn't serve its roster: no count either,
                  since "23 members you cannot see" is the fact somebody
                  turning the roster off was trying not to publish. */}
              {sys.member_count !== null && (
                <p className="mt-0.5 flex items-center justify-center gap-1 text-xs text-muted-foreground">
                  <Users className="h-3 w-3" />
                  {sys.member_count} member{sys.member_count === 1 ? "" : "s"}
                </p>
              )}
            </div>
            {sys.description && (
              <div className="prose prose-sm dark:prose-invert max-w-none text-left">
                <Suspense fallback={<p className="text-sm">{sys.description}</p>}>
                  <MarkdownPreview content={sys.description} publicSurface />
                </Suspense>
              </div>
            )}
          </div>

          {/* Who is fronting right now is the liveliest thing on the page and
              the only part that changes while it is open, so it gets a card of
              its own directly under the header rather than a tab you have to
              go looking for. */}
          {fronting.data && <FrontingSection fronting={fronting.data} />}

          {tabs.length > 0 && activeTab && (
            <Tabs value={activeTab} onValueChange={setTab} className="w-full">
              <TabsList className="w-full">
                {tabs.map((t) => (
                  <TabsTrigger key={t.value} value={t.value} className="flex-1">
                    {t.label}
                  </TabsTrigger>
                ))}
              </TabsList>

              {memberList && (
                <TabsContent value="members" className="space-y-3">
                  <MembersSection members={memberList} fronting={fronting.data ?? null} />
                </TabsContent>
              )}

              {groups.data && (
                <TabsContent value="groups" className="space-y-3">
                  <GroupsSection view={groups.data} />
                </TabsContent>
              )}

              {relationships.data && (
                <TabsContent value="relationships">
                  <RelationshipsSection view={relationships.data} />
                </TabsContent>
              )}
            </Tabs>
          )}

          <PoweredBy />
        </div>
      </div>

      {/* No permalinks on this view: a member opens in place, with no URL to
          send anyone. */}
      <Dialog
        open={Boolean(openMember)}
        onOpenChange={(open) => !open && setOpenMemberId(null)}
      >
        <DialogContent className="gap-0 overflow-hidden p-0 sm:max-w-md">
          {openMember && (
            <>
              <DialogHeader className="sr-only">
                <DialogTitle>{openMember.display_name || openMember.name}</DialogTitle>
              </DialogHeader>
              <MemberCardBody member={openMember} linkName={false} />
            </>
          )}
        </DialogContent>
      </Dialog>
    </MemberNavContext.Provider>
  );
}

function MembersSection({
  members,
  fronting,
}: {
  members: PublicMemberView[];
  fronting: PublicFrontingView | null;
}) {
  if (members.length === 0) {
    return (
      <p className="text-center text-sm text-muted-foreground">
        No members are shared on this profile.
      </p>
    );
  }
  if (members.length > LARGE_SYSTEM_MEMBERS) {
    return <LargeMemberList members={members} fronting={fronting} />;
  }
  return (
    <>
      {members.map((m) => (
        <MemberCard key={m.id} member={m} />
      ))}
    </>
  );
}

/** The members tab for a system with more members than anyone wants to scroll
 *  past: whoever is fronting first, in full, then everybody else behind a
 *  toggle with a search box. Expanded state lives for as long as the page is
 *  open and no longer - it is a browsing convenience, not a preference. */
function LargeMemberList({
  members,
  fronting,
}: {
  members: PublicMemberView[];
  fronting: PublicFrontingView | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState("");

  const frontingIds = useMemo(
    () => new Set((fronting?.members ?? []).map((m) => m.id)),
    [fronting],
  );
  const featured = members.filter((m) => frontingIds.has(m.id));
  const rest = members.filter((m) => !frontingIds.has(m.id));

  const needle = query.trim().toLowerCase();
  const filtered = needle
    ? rest.filter((m) =>
        [m.name, m.display_name, m.pronouns].some((v) =>
          v ? v.toLowerCase().includes(needle) : false,
        ),
      )
    : rest;

  return (
    <div className="space-y-3">
      {featured.length > 0 && (
        <div className="space-y-3">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Fronting right now
          </p>
          {featured.map((m) => (
            <MemberCard key={m.id} member={m} />
          ))}
        </div>
      )}

      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium transition-colors hover:bg-muted/60"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <span>
          {expanded
            ? "Hide the list"
            : featured.length > 0
              ? `Show the other ${rest.length} members`
              : `Show all ${rest.length} members`}
        </span>
      </button>

      {expanded && (
        <div className="space-y-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by name or pronouns"
              aria-label="Search members"
              className="pl-9"
            />
          </div>
          {filtered.map((m) => (
            <MemberCard key={m.id} member={m} />
          ))}
          {filtered.length === 0 && (
            <p className="text-center text-sm text-muted-foreground">
              No members match that search.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function FrontingSection({ fronting }: { fronting: PublicFrontingView }) {
  const { members, hidden_count } = fronting;
  if (members.length === 0 && hidden_count === 0) {
    return (
      <Card>
        <CardContent className="p-4 text-sm text-muted-foreground">
          No one is fronting right now.
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardContent className="space-y-2 p-4">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Currently fronting
        </p>
        <div className="flex flex-wrap gap-2">
          {members.map((m) => (
            <MemberChip
              key={m.id}
              id={m.id}
              name={m.display_name || m.name}
              avatarUrl={m.avatar_url}
              color={m.color}
            />
          ))}
        </div>
        {hidden_count > 0 && (
          <p className="text-xs text-muted-foreground">
            and {hidden_count} other{hidden_count === 1 ? "" : "s"} not shown here.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function GroupsSection({ view }: { view: PublicGroupsView }) {
  const { groups } = view;
  if (groups.length === 0) {
    return (
      <Card>
        <CardContent className="p-4 text-sm text-muted-foreground">
          No groups are shared on this profile.
        </CardContent>
      </Card>
    );
  }
  return (
    <>
      {groups.map((g) => (
        <GroupCard key={g.id} group={g} />
      ))}
    </>
  );
}

function GroupCard({ group }: { group: PublicGroupView }) {
  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center gap-2">
          <ColorDot color={group.color} />
          <p className="font-medium">{group.name}</p>
        </div>
        {group.description && (
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <Suspense fallback={<p className="text-sm">{group.description}</p>}>
              <MarkdownPreview content={group.description} publicSurface />
            </Suspense>
          </div>
        )}
        {/* A published group lists only the members this view already shows,
            so an empty list is a normal state and not an error: the group's
            name and description are still the owner's to publish. */}
        {group.members.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {group.members.map((m) => (
              <MemberChip key={m.id} id={m.id} name={m.name} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** A member as a chip: fronting and group rosters both render this. Anything
 *  the payload doesn't carry (a group member is id and name only) is filled in
 *  from the roster card when we have it. */
function MemberChip({
  id,
  name,
  avatarUrl = null,
  color = null,
}: {
  id: string;
  name: string;
  avatarUrl?: string | null;
  color?: string | null;
}) {
  const nav = useContext(MemberNavContext);
  const card = nav?.card(id);
  const avatar = avatarUrl ?? card?.avatar_url ?? null;
  const dot = color ?? card?.color ?? null;
  return (
    <MemberOpen
      id={id}
      className="flex items-center gap-2 rounded-full border py-1 pl-1 pr-3 text-sm"
    >
      <Avatar className="size-6">
        {avatar && isPublicImageAllowed(avatar) && <AvatarImage src={avatar} />}
        <AvatarFallback
          className="text-[10px]"
          style={dot ? { backgroundColor: dot, color: "#fff" } : undefined}
        >
          {name.charAt(0).toUpperCase()}
        </AvatarFallback>
      </Avatar>
      <span>{name}</span>
    </MemberOpen>
  );
}

/**
 * Relationships as the same force-directed graph the owner sees, with every
 * owner affordance simply not passed: no edit mode, no edge dialog, nothing to
 * change. Pan, zoom and nudge still work, because looking around a picture is
 * part of looking at it.
 *
 * The list underneath is not a fallback, it is the readable version: it says
 * the relationship in words ("A is the parent of B"), it is what a screen
 * reader gets, and it is where each end is a link to that member's card.
 */
function RelationshipsSection({ view }: { view: PublicRelationshipsView }) {
  const { relationships } = view;
  const nav = useContext(MemberNavContext);
  const navigate = useNavigate();

  // The nodes come out of the relationships themselves: every published
  // relationship names both of its ends, so the graph stands up on its own
  // even when the view serves no member list at all. Colour is the one thing
  // the relationship payload can't say, so it is joined from the roster when
  // there is one, and left neutral when there isn't.
  const nodes = useMemo<GraphNode[]>(() => {
    const seen = new Map<string, GraphNode>();
    for (const r of relationships) {
      for (const end of [r.source, r.target]) {
        if (!seen.has(end.id)) {
          seen.set(end.id, {
            id: end.id,
            name: end.name,
            color: nav?.card(end.id)?.color ?? null,
          });
        }
      }
    }
    return [...seen.values()];
  }, [relationships, nav]);

  const edges = useMemo<GraphEdge[]>(
    () =>
      relationships.map((r) => ({
        id: r.id,
        source_id: r.source.id,
        target_id: r.target.id,
        source_label: r.source_label,
        target_label: r.target_label,
        mutual: r.mutual,
        color: r.type_color,
      })),
    [relationships],
  );

  /** A node opens the member the same way their name does anywhere else on
   *  this page: their own page where the view publishes member links, in place
   *  where it doesn't. A node this page holds no card for is inert, exactly as
   *  their name is in the list below. */
  function openNode(id: string) {
    if (!nav?.card(id)) return;
    const href = nav.linkTo?.(id);
    if (href) navigate(href);
    else nav.open(id);
  }

  if (relationships.length === 0) {
    return (
      <Card>
        <CardContent className="p-4 text-sm text-muted-foreground">
          No relationships are shared on this profile.
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-3">
      <RelationshipGraphCanvas
        nodes={nodes}
        edges={edges}
        onNodeClick={openNode}
        className="h-[55vh] min-h-[360px]"
        touchAction="pan-y"
        ariaLabel={`Relationship graph: ${nodes.length} member${
          nodes.length === 1 ? "" : "s"
        } joined by ${relationships.length} relationship${
          relationships.length === 1 ? "" : "s"
        }. Every one of them is listed in words below the graph.`}
      />
      <p className="text-xs text-muted-foreground">
        Drag to pan, scroll to zoom, drag a member to nudge them.
      </p>
      <Separator />
      <Card>
        <CardContent className="space-y-2 p-4">
          {relationships.map((r) => (
            <RelationshipRow key={r.id} relationship={r} />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function RelationshipRow({ relationship: r }: { relationship: PublicRelationship }) {
  // Both ends read the same way for a symmetric type and for a mutual
  // either-edge; `isDirectedEdge` is the same rule the graph draws its arrows
  // by, so the row and the line can't disagree about which way a relationship
  // points. No arrow means the single label speaks for both ends.
  const undirected = !isDirectedEdge(r);
  const iconCls = "h-3.5 w-3.5 shrink-0 text-muted-foreground";
  const endCls = "truncate rounded px-1 font-medium";

  return (
    <div className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
      <ColorDot color={r.type_color} />
      <span className="flex min-w-0 flex-1 items-center gap-2 truncate">
        <MemberOpen id={r.source.id} className={endCls}>
          {r.source.name}
        </MemberOpen>
        {undirected ? (
          <ArrowLeftRight className={iconCls} aria-label="mutual" />
        ) : (
          <ArrowRight className={iconCls} aria-label="one-way" />
        )}
        <MemberOpen id={r.target.id} className={endCls}>
          {r.target.name}
        </MemberOpen>
      </span>
      <span className="shrink-0 text-xs text-muted-foreground">
        {undirected ? r.source_label : `${r.source_label} / ${r.target_label}`}
      </span>
    </div>
  );
}

function MemberCard({ member }: { member: PublicMemberView }) {
  return (
    <Card className="overflow-hidden">
      <MemberCardBody member={member} />
    </Card>
  );
}

/**
 * The member card's content, shared by the list, the in-page dialog and the
 * member's own page so all three say exactly the same thing.
 *
 * `linkName` is off for the two places that are already showing this member:
 * opening them from there would go nowhere.
 */
function MemberCardBody({
  member,
  linkName = true,
}: {
  member: PublicMemberView;
  linkName?: boolean;
}) {
  const fields = Object.entries(member.fields ?? {});
  const header = (
    <>
      <Avatar className="size-12">
        {member.avatar_url && isPublicImageAllowed(member.avatar_url) && (
          <AvatarImage src={member.avatar_url} />
        )}
        <AvatarFallback
          style={member.color ? { backgroundColor: member.color, color: "#fff" } : undefined}
        >
          {(member.display_name || member.name).charAt(0).toUpperCase()}
        </AvatarFallback>
      </Avatar>
      <span className="min-w-0">
        <span className="block truncate font-medium">
          {member.display_name || member.name}
        </span>
        {member.pronouns && (
          <span className="block text-xs text-muted-foreground">{member.pronouns}</span>
        )}
      </span>
    </>
  );

  return (
    <>
      {member.banner_url && isPublicImageAllowed(member.banner_url) && (
        <div className="h-24 w-full bg-muted">
          <img src={member.banner_url} alt="" className="h-full w-full object-cover" />
        </div>
      )}
      <CardContent className="space-y-3 p-4">
        {linkName ? (
          <MemberOpen
            id={member.id}
            className="-mx-1 flex items-center gap-3 rounded-md px-1 py-1"
          >
            {header}
          </MemberOpen>
        ) : (
          <span className="flex items-center gap-3 px-1 py-1">{header}</span>
        )}
        {member.bio && (
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <Suspense fallback={<p className="text-sm">{member.bio}</p>}>
              <MarkdownPreview content={member.bio} publicSurface />
            </Suspense>
          </div>
        )}
        {fields.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {fields.map(([k, v]) => (
              <Badge key={k} variant="secondary" className="font-normal">
                <span className="text-muted-foreground">{k}:</span>&nbsp;
                {formatFieldValue(v)}
              </Badge>
            ))}
          </div>
        )}
      </CardContent>
    </>
  );
}

function formatFieldValue(v: unknown): string {
  if (v == null) return "";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "boolean") return v ? "Yes" : "No";
  // A custom field can hold an arbitrary JSON value; String() on an object
  // renders "[object Object]", so show the shape instead.
  if (typeof v === "object") {
    try {
      return JSON.stringify(v);
    } catch {
      return "";
    }
  }
  return String(v);
}

/**
 * The public footer: attribution, plus the operator's abuse/DMCA contact when
 * they wrote one. Lives here rather than in the shared app footer because
 * these are the only pages someone with no account can reach, so they are the
 * only ones where "who do I tell about this?" has nowhere else to go.
 */
function PoweredBy() {
  const { data: config } = useQuery({
    queryKey: ["auth-config"],
    queryFn: getAuthConfig,
  });
  const [abuseOpen, setAbuseOpen] = useState(false);
  const abuse = config?.abuse_contact;

  return (
    <div className="flex items-center justify-center gap-1.5 pt-4 text-xs text-muted-foreground">
      <Logo className="h-4 w-4 rounded" />
      <span>Powered by Sheaf</span>
      {abuse && (
        <>
          <span aria-hidden="true">·</span>
          <button
            type="button"
            onClick={() => setAbuseOpen(true)}
            className="transition-colors hover:text-foreground hover:underline"
          >
            Abuse / DMCA
          </button>
          <Dialog open={abuseOpen} onOpenChange={setAbuseOpen}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Abuse and DMCA contact</DialogTitle>
              </DialogHeader>
              {/* Same rendering pipeline as a public bio: no external images,
                  and mailto:/https: links stay clickable, which is the entire
                  point of the thing. */}
              <Suspense
                fallback={<p className="text-sm whitespace-pre-wrap">{abuse}</p>}
              >
                <MarkdownPreview content={abuse} publicSurface />
              </Suspense>
            </DialogContent>
          </Dialog>
        </>
      )}
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      {children}
    </div>
  );
}

function NotAvailable() {
  return (
    <Centered>
      <div className="w-full max-w-md space-y-4 text-center">
        <Logo className="mx-auto h-10 w-10 rounded-md" />
        <h1 className="text-xl font-semibold">Profile not available</h1>
        <p className="text-sm text-muted-foreground">
          This profile is private, doesn't exist, or the link is no longer
          active.
        </p>
      </div>
    </Centered>
  );
}

/**
 * One member at their own address, for views that publish member permalinks.
 *
 * Fetches the member rather than reading them out of the list, because this
 * page is something you can be linked straight to. Every reason it might not
 * resolve - no permalinks on this view, no roster, not that member - is the
 * same 404, and renders the same "not available" as the profile itself.
 */
function PublicMemberPageView({
  source,
  memberId,
}: {
  source: Source;
  memberId: string;
}) {
  useNoIndex();

  const system = useQuery<PublicSystemView>({
    queryKey: ["public", sourceKey(source), "system"],
    queryFn: () => getPublicSystem(source),
    retry: false,
  });

  const member = useQuery<PublicMemberView>({
    queryKey: ["public", sourceKey(source), "member", memberId],
    queryFn: () => getPublicMember(source, memberId),
    retry: false,
  });

  if (system.isLoading || member.isLoading) {
    return (
      <Centered>
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </Centered>
    );
  }

  if (member.isError || !member.data) {
    return <NotAvailable />;
  }

  const sys = system.data;
  const accent = sys?.color ?? undefined;

  return (
    <div className="min-h-screen bg-muted/20">
      <div className="h-2 w-full" style={accent ? { backgroundColor: accent } : undefined} />
      <div className="mx-auto w-full max-w-2xl px-4 py-8 space-y-6">
        <Link
          to={profilePath(source)}
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          {sys ? `Back to ${sys.name}` : "Back to the profile"}
        </Link>

        <Card className="overflow-hidden">
          <MemberCardBody member={member.data} linkName={false} />
        </Card>

        <PoweredBy />
      </div>
    </div>
  );
}

// --- Route wrappers ---

export function PublicSystemProfilePage() {
  const { systemId } = useParams<{ systemId: string }>();
  if (!systemId) return <NotAvailable />;
  return <PublicProfileView source={{ kind: "system", systemId }} />;
}

export function SharedViewPage() {
  const { token } = useParams<{ token: string }>();
  if (!token) return <NotAvailable />;
  return <PublicProfileView source={{ kind: "link", token }} />;
}

export function PublicSystemMemberPage() {
  const { systemId, memberId } = useParams<{ systemId: string; memberId: string }>();
  if (!systemId || !memberId) return <NotAvailable />;
  return (
    <PublicMemberPageView source={{ kind: "system", systemId }} memberId={memberId} />
  );
}

export function SharedViewMemberPage() {
  const { token, memberId } = useParams<{ token: string; memberId: string }>();
  if (!token || !memberId) return <NotAvailable />;
  return <PublicMemberPageView source={{ kind: "link", token }} memberId={memberId} />;
}
