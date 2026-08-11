import { lazy, Suspense, useEffect } from "react";
import { useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftRight, ArrowRight, Loader2, Users } from "lucide-react";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ColorDot } from "@/components/color-dot";
import { Logo } from "@/components/logo";
import { ApiError } from "@/lib/api-error";
import { isPublicImageAllowed } from "@/lib/image-sources";
import {
  getPublicFronting,
  getPublicMembers,
  getPublicRelationships,
  getPublicSystem,
} from "@/lib/public-profiles";
import type {
  PublicFrontingView,
  PublicMemberView,
  PublicRelationship,
  PublicRelationshipsView,
  PublicSystemView,
} from "@/types/api";

const MarkdownPreview = lazy(() =>
  import("@/components/bio-editor").then((m) => ({ default: m.MarkdownPreview })),
);

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

export function PublicProfileView({ source }: { source: Source }) {
  useNoIndex();

  const system = useQuery<PublicSystemView>({
    queryKey: ["public", sourceKey(source), "system"],
    queryFn: () => getPublicSystem(source),
    retry: false,
  });

  const members = useQuery<PublicMemberView[]>({
    queryKey: ["public", sourceKey(source), "members"],
    queryFn: () => getPublicMembers(source),
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

  return (
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
            <p className="mt-0.5 flex items-center justify-center gap-1 text-xs text-muted-foreground">
              <Users className="h-3 w-3" />
              {sys.member_count} member{sys.member_count === 1 ? "" : "s"}
            </p>
          </div>
          {sys.description && (
            <div className="prose prose-sm dark:prose-invert max-w-none text-left">
              <Suspense fallback={<p className="text-sm">{sys.description}</p>}>
                <MarkdownPreview content={sys.description} publicSurface />
              </Suspense>
            </div>
          )}
        </div>

        <Tabs defaultValue="members" className="w-full">
          <TabsList className="w-full">
            <TabsTrigger value="members" className="flex-1">
              Members
            </TabsTrigger>
            {fronting.data && (
              <TabsTrigger value="fronting" className="flex-1">
                Fronting
              </TabsTrigger>
            )}
            {relationships.data && (
              <TabsTrigger value="relationships" className="flex-1">
                Relationships
              </TabsTrigger>
            )}
          </TabsList>

          <TabsContent value="members" className="space-y-3">
            {(members.data ?? []).map((m) => (
              <MemberCard key={m.id} member={m} />
            ))}
            {members.isSuccess && (members.data?.length ?? 0) === 0 && (
              <p className="text-center text-sm text-muted-foreground">
                No members are shared on this profile.
              </p>
            )}
          </TabsContent>

          {fronting.data && (
            <TabsContent value="fronting">
              <FrontingSection fronting={fronting.data} />
            </TabsContent>
          )}

          {relationships.data && (
            <TabsContent value="relationships">
              <RelationshipsSection view={relationships.data} />
            </TabsContent>
          )}
        </Tabs>

        <div className="flex items-center justify-center gap-1.5 pt-4 text-xs text-muted-foreground">
          <Logo className="h-4 w-4 rounded" />
          <span>Powered by Sheaf</span>
        </div>
      </div>
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
            <div
              key={m.id}
              className="flex items-center gap-2 rounded-full border py-1 pl-1 pr-3"
            >
              <Avatar className="size-6">
                {m.avatar_url && isPublicImageAllowed(m.avatar_url) && (
                  <AvatarImage src={m.avatar_url} />
                )}
                <AvatarFallback
                  className="text-[10px]"
                  style={m.color ? { backgroundColor: m.color, color: "#fff" } : undefined}
                >
                  {(m.display_name || m.name).charAt(0).toUpperCase()}
                </AvatarFallback>
              </Avatar>
              <span className="text-sm">{m.display_name || m.name}</span>
            </div>
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

function RelationshipsSection({ view }: { view: PublicRelationshipsView }) {
  const { relationships } = view;
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
    <Card>
      <CardContent className="space-y-2 p-4">
        {relationships.map((r) => (
          <RelationshipRow key={r.id} relationship={r} />
        ))}
      </CardContent>
    </Card>
  );
}

function RelationshipRow({ relationship: r }: { relationship: PublicRelationship }) {
  // Both ends read the same way for a symmetric type and for a mutual
  // either-edge, and the server says so twice over: `mutual`, and two labels
  // that come back identical. Either one means there is no direction to draw,
  // so no arrow is drawn and the single label speaks for both ends.
  const undirected = r.mutual || r.source_label === r.target_label;
  const iconCls = "h-3.5 w-3.5 shrink-0 text-muted-foreground";

  return (
    <div className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
      <ColorDot color={r.type_color} />
      <span className="flex min-w-0 flex-1 items-center gap-2 truncate">
        <span className="truncate font-medium">{r.source.name}</span>
        {undirected ? (
          <ArrowLeftRight className={iconCls} aria-label="mutual" />
        ) : (
          <ArrowRight className={iconCls} aria-label="one-way" />
        )}
        <span className="truncate font-medium">{r.target.name}</span>
      </span>
      <span className="shrink-0 text-xs text-muted-foreground">
        {undirected ? r.source_label : `${r.source_label} / ${r.target_label}`}
      </span>
    </div>
  );
}

function MemberCard({ member }: { member: PublicMemberView }) {
  const fields = Object.entries(member.fields ?? {});
  return (
    <Card className="overflow-hidden">
      {member.banner_url && isPublicImageAllowed(member.banner_url) && (
        <div className="h-24 w-full bg-muted">
          <img
            src={member.banner_url}
            alt=""
            className="h-full w-full object-cover"
          />
        </div>
      )}
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center gap-3">
          <Avatar className="size-12">
            {member.avatar_url &&
              isPublicImageAllowed(member.avatar_url) && (
                <AvatarImage src={member.avatar_url} />
              )}
            <AvatarFallback
              style={member.color ? { backgroundColor: member.color, color: "#fff" } : undefined}
            >
              {(member.display_name || member.name).charAt(0).toUpperCase()}
            </AvatarFallback>
          </Avatar>
          <div className="min-w-0">
            <p className="font-medium">{member.display_name || member.name}</p>
            {member.pronouns && (
              <p className="text-xs text-muted-foreground">{member.pronouns}</p>
            )}
          </div>
        </div>
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
    </Card>
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
