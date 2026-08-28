import { type FormEvent, type MouseEvent, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy, ExternalLink, Eye, Globe, Link2, Trash2 } from "lucide-react";

import {
  useShareViews,
  useShareGrants,
  useShareAudit,
  useCreateShareView,
  useUpdateShareView,
  useDeleteShareView,
  useAddViewMember,
  useRemoveViewMember,
  useAddViewGroup,
  useRemoveViewGroup,
  useAddViewField,
  useRemoveViewField,
  useCreateShareGrant,
  useRotateShareGrant,
  useRevokeShareGrant,
  useAttestAdult,
  useSharePreview,
} from "@/hooks/use-sharing";
import { PublicProfileBody } from "@/routes/public-profile";
import { useMembers } from "@/hooks/use-members";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { useGroups } from "@/hooks/use-groups";
import { useCustomFields } from "@/hooks/use-custom-fields";
import { useAuth } from "@/hooks/use-auth";
import {
  apiErrorMessage,
  isStepUpRequiredError,
  showApiErrorToast,
} from "@/lib/api-errors";
import { getSystemSafety } from "@/lib/system-safety";
import { getMySystem } from "@/lib/systems";
import type {
  ShareAuditEntry,
  ShareGrant,
  ShareGrantCreated,
  ShareNotServedReason,
  ShareView,
  ShareViewCreate,
  ShareViewGroupRow,
  ShareViewMemberRow,
} from "@/types/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DestructiveConfirmDialog } from "@/components/destructive-confirm-dialog";
import { PageHeader } from "@/components/page-header";
import type { DeleteConfirmation, DestructiveConfirm } from "@/types/api";

/**
 * Sharing lives at the top level rather than under Settings: it is somewhere
 * you go to do a thing (publish, revoke, check who can see what), not a
 * preference you set once. The page supplies its own header and column, which
 * the settings layout used to provide.
 *
 * With the instance's public surface off, this page used to be nothing but the
 * "off" card - which hid the audit and every revoke button behind a setting
 * only the operator controls, while grants made earlier sat on disk waiting for
 * it to come back. So the off state now adds a banner rather than replacing the
 * page: everything that takes exposure BACK stays here and stays working, and
 * only the controls that would publish something new are held shut.
 */
export function SharingPage() {
  const off = useSharingOff();
  return (
    <>
      <PageHeader title="Sharing" />
      <div className="grid gap-6 max-w-2xl">
        {off && <SharingOffCard />}
        <SharingManager />
      </div>
    </>
  );
}

/** Is this instance's public surface switched off?
 *
 *  Read from the session wherever it is needed rather than threaded down as a
 *  prop: it is instance state, not a decision this page makes, and the controls
 *  that have to react to it sit several components deep. */
function useSharingOff(): boolean {
  const { user } = useAuth();
  return Boolean(user && !user.public_profiles_enabled);
}

/** What the operator's switch means for the things you already published. The
 *  second paragraph is the load-bearing one: the setting stops the serving, it
 *  does not revoke anything, so "off" is not the same as "gone". */
function SharingOffCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Sharing is off here</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-sm text-muted-foreground">
          Public profiles and share links are turned off on this instance, so
          nothing below is reaching anyone right now. The server operator can
          turn them on by setting{" "}
          <code className="rounded bg-muted px-1 py-0.5 text-xs">
            PUBLIC_PROFILES_ENABLED=true
          </code>
          .
        </p>
        <p className="text-[11px] text-amber-600 dark:text-amber-500">
          Anything published before it was turned off is kept exactly as it was,
          and it starts serving again if the setting ever comes back. Nothing new
          can be published while it is off, but unpublishing, rotating a link and
          narrowing a view all still work - so if there is something you would
          not want to reappear, unpublish it now rather than relying on the
          setting.
        </p>
      </CardContent>
    </Card>
  );
}

/** The master switch is off: system privacy is not Public, so the WHOLE public
 *  surface is dark regardless of any grant or view flag below. This used to be
 *  a single line buried in the audit card at the bottom, which is not loud
 *  enough - it caught people out, who published a view and could not see why a
 *  visitor got nothing. So it gets the same top-of-page treatment as the
 *  instance-off card. Shown only for `system_private` (the owner can fix it in
 *  one click); the `account_state` case is operator-side and is never surfaced
 *  as something the owner should go and change. Independent of the safety
 *  category - this is about the switch being off, not about staging. */
function SystemPrivateCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Your system is not public</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-sm text-muted-foreground">
          Your system's privacy is not set to Public, so nothing here reaches
          anyone. Views and links only serve while your system is Public.
        </p>
        <p className="text-[11px] text-amber-600 dark:text-amber-500">
          It is the master switch over everything you share. Change it under
          Settings, system profile to serve these again. Nothing below has been
          revoked - it all comes back the moment you set your system to Public.
        </p>
      </CardContent>
    </Card>
  );
}

/** An operator has disabled publishing on this system (the admin revoke-all
 *  takedown latches it). Distinct from the system-private card: that one is a
 *  switch the owner can flip back themselves, this one they cannot - only an
 *  admin lifts it. It is the strongest "nothing serves and you cannot change
 *  that" state on the page, so it rides above every other banner. While it is
 *  set the owner can still revoke, rotate and narrow (taking MORE down is
 *  always allowed); only publishing something new is held shut. */
function PublishingBlockedCard() {
  return (
    <Card className="border-destructive/50">
      <CardHeader>
        <CardTitle className="text-base">Publishing is disabled by an operator</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-sm text-muted-foreground">
          An operator on this instance has disabled publishing on your system.
          Nothing new can be published and your system cannot be set to Public
          until they lift it. This is not something you can change from here.
        </p>
        <p className="text-[11px] text-amber-600 dark:text-amber-500">
          You can still revoke grants, rotate links and narrow views - taking
          things down always works. If you think this is a mistake, contact the
          instance operator.
        </p>
      </CardContent>
    </Card>
  );
}

/**
 * Re-auth context shared by every exposing action on the page.
 *
 * Two separate questions, kept apart because the server keeps them apart. They
 * used to be one boolean here, and because that boolean also demanded a grace
 * period, the default setup - category armed, grace at 0, a password tier -
 * showed no credential field, sent no credentials, and collected a 400
 * "Password required" with nowhere to type it.
 */
interface SafetyContext {
  /** The profile_visibility safety category is armed, so an action that
   *  actually reaches a reader has to clear re-auth first. Independent of the
   *  grace period, exactly as `visibility_step_up_required` is server-side: a
   *  re-authed raise that applies at once is still a real protection. Drives
   *  whether a credential field is shown, never the copy. */
  stepUp: boolean;
  /** ...and there is a window for the change to wait out, so it parks pending
   *  instead of going live. Mirrors `visibility_grace_days() > 0`, which reads
   *  as 0 whenever the category is disarmed. Drives the copy, never a field. */
  staged: boolean;
  tier: DeleteConfirmation;
}

/** The sentence every "you are about to expose something" dialog ends on.
 *  Promising a grace period that is not configured would be a lie about the
 *  one thing the owner is being asked to weigh. */
function effectSentence(safety: SafetyContext): string {
  return safety.staged
    ? "It takes effect after your grace period."
    : "It takes effect immediately.";
}

/** Every view flag that shows MORE when it is turned on, in one list (the
 *  client-side mirror of the server's own). The pending badge and the
 *  step-up gate below both derive from it, so a flag added here is covered
 *  everywhere instead of being covered in two places out of three. */
const EXPOSURE_FLAGS = [
  "include_members",
  "include_bio",
  "include_fronting",
  "fronting_show_count",
  "include_relationships",
  "include_groups",
] as const;

// `member_permalinks` is deliberately NOT in that list. It stages nothing
// because it exposes nothing new - a permalink is only a stable address for a
// member the view already shows - so it must not ride the staged-flag
// machinery, which would make it demand a step-up and sit out a grace period
// for a change that reveals no one.

type ExposureFlag = (typeof EXPOSURE_FLAGS)[number];

/** The audit lists what each grant WOULD serve. When the account-level
 *  suppression is set, none of it is reachable right now, so the list needs a
 *  line above it saying so - otherwise it reads as a page that is live.
 *  `publishing_blocked` outranks the other two server-side, so its line is the
 *  one that shows when an operator has latched the system shut. */
function suppressionNotice(reason: string | null): string | null {
  if (reason === "publishing_blocked") {
    return (
      "None of this is being served right now: an operator on this instance " +
      "has disabled publishing on your system. Nothing below has been " +
      "deleted, and it serves again if they lift it."
    );
  }
  if (reason === "system_private") {
    return (
      "None of this is reachable right now: your system's privacy is not set " +
      "to Public, and that is the master switch over everything you share. " +
      "Change it under Settings - system profile to serve these again."
    );
  }
  if (reason === "account_state") {
    return (
      "None of this is being served right now because of the state of your " +
      "account. Nothing has been deleted and everything below is still set up " +
      "exactly as you left it; it comes back when your account does."
    );
  }
  return null;
}

/** The member half of one audit line.
 *
 *  The audit's job is "who can currently see what", so the number that leads
 *  is the number of people a visitor actually gets - the same basis as the
 *  field, relationship and group counts beside it. The curated total is still
 *  worth showing when it is bigger, because the gap is the interesting part
 *  ("I put five people in and only three show"), but it is not the headline:
 *  quoting it alone is what let the audit over-report for years' worth of
 *  archived, private and deletion-queued members.
 *
 *  Falls back to the curated count against a server that predates
 *  `served_member_count`, which is the old behaviour rather than a claim of
 *  zero. */
function servedMembersLabel(e: ShareAuditEntry): string {
  const served = e.served_member_count ?? e.member_count;
  const noun = `member${served === 1 ? "" : "s"}`;
  return served < e.member_count
    ? `${served} of ${e.member_count} ${noun}`
    : `${served} ${noun}`;
}

/** What a member badge says when the view is holding them back, keyed by the
 *  server's reason. The server decides WHETHER somebody shows (it composes the
 *  projection's own filter); this only decides how to word it.
 *
 *  `pending` is absent on purpose: a pending member is not being held back,
 *  they are waiting out a grace window, and the row already carries its own
 *  "pending" badge saying so. Each entry points at the control in the member
 *  editor that clears it, and uses that control's own wording, so the two
 *  screens cannot describe the same state differently. */
const NOT_SERVED_COPY: Record<
  Exclude<ShareNotServedReason, "pending">,
  { badge: string; title: string }
> = {
  never_shareable: {
    badge: "won't show: never shareable",
    title:
      "This member is marked never shareable, so they can never appear in " +
      "any view. Clear that in the member editor to show them.",
  },
  deletion_queued: {
    badge: "won't show: deleting",
    title:
      "This member is queued for deletion, so they stopped being shown the " +
      "moment you asked for that. Cancel the deletion to show them again.",
  },
  archived: {
    badge: "won't show: archived",
    title:
      "This member is archived, so they are hidden from shared pages just " +
      "as they are from your own lists. Unarchive them to show them again.",
  },
  private: {
    badge: "won't show",
    title:
      "This member's privacy isn't Public, so they won't appear. Set their " +
      "privacy to Public to show them.",
  },
};

/** The copy for one member row, or null when they are actually being served.
 *  A row the server says is not served but cannot name a reason for reads as a
 *  plain "won't show" rather than an invented explanation. */
function notServedCopy(
  row: ShareViewMemberRow,
): { badge: string; title: string } | null {
  if (row.served !== false) return null;
  if (row.not_served_reason === "pending") return null;
  if (row.not_served_reason && row.not_served_reason in NOT_SERVED_COPY) {
    return NOT_SERVED_COPY[
      row.not_served_reason as Exclude<ShareNotServedReason, "pending">
    ];
  }
  return {
    badge: "won't show",
    title: "This member isn't being shown on the published page.",
  };
}

function SharingManager() {
  const off = useSharingOff();
  const { data: views } = useShareViews();
  const { data: grants } = useShareGrants();
  const { data: audit } = useShareAudit();
  const { data: safety } = useQuery({
    queryKey: ["system-safety"],
    queryFn: getSystemSafety,
  });
  const { data: system } = useQuery({ queryKey: ["system", "me"], queryFn: getMySystem });

  const safetyCtx: SafetyContext = useMemo(() => {
    const s = safety?.settings;
    // The category being armed is the whole of the step-up question; the grace
    // period is the whole of the staging question, and only counts while the
    // category is armed (a window over a disarmed category stages nothing).
    const armed = !!s && s.applies_to_profile_visibility;
    return {
      stepUp: armed,
      staged: armed && (s?.grace_period_days ?? 0) > 0,
      tier: s?.auth_tier ?? "none",
    };
  }, [safety]);

  const grantsByView = useMemo(() => {
    const m = new Map<string, ShareGrant[]>();
    for (const g of grants ?? []) {
      if (g.status === "revoked") continue;
      const arr = m.get(g.view_id) ?? [];
      arr.push(g);
      m.set(g.view_id, arr);
    }
    return m;
  }, [grants]);

  // The instance switch is the outermost reason nothing below is reachable, so
  // it wins the one notice slot: an owner reading a list of live-looking grants
  // on an instance with sharing off needs to know both that they are serving
  // nobody and that they have not been revoked.
  const suppressedNotice = off
    ? "None of this is reachable right now: public profiles and share links " +
      "are turned off on this instance. Every grant below is retained as it " +
      "was and starts serving again if that setting is turned back on."
    : suppressionNotice(audit?.profile_suppressed ?? null);

  // The master-switch banner rides at the very top, above "How sharing works",
  // so an owner sees WHY nothing serves before they read how any of it works.
  // Suppressed when the instance switch is off: that card already sits above
  // this manager and is the outermost reason, so showing both would just be two
  // "nothing serves" cards competing for the same point.
  const systemPrivate =
    !off && audit?.profile_suppressed === "system_private";

  // The operator takedown latch. Read straight off the system read, and shown
  // above every other banner: it is the one state on this page the owner cannot
  // resolve themselves, so it must not sit under a card offering a fix.
  const publishingBlocked = Boolean(system?.publishing_blocked);

  return (
    <>
      {publishingBlocked && <PublishingBlockedCard />}
      {systemPrivate && <SystemPrivateCard />}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">How sharing works</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>
            A <strong>view</strong> is a curated selection of exactly which
            members and custom fields are shown. A view is visible to no one
            until you publish it, either as a public profile or behind a
            revocable link. No member and no custom field is ever shown that you
            did not add to a view.
          </p>
          <p>
            Your system's own details are the exception, and publishing anything
            at all shows them: the page is headed by your system's{" "}
            <strong>name, avatar, colour, tag and description</strong>, whatever
            the view contains. A public profile carries your system's id in its
            address; a share link uses an opaque token instead, and your id
            appears nowhere in what that link serves - so two links, or a link
            and your public profile, cannot be matched up as belonging to the
            same system by whoever holds them. The number of members is shown
            only when the view serves its member list.
          </p>
          <p>
            Images you uploaded show up on a shared page as normal. Images
            linked from another site do not: loading one would tell that site
            the address of everyone who opens your page, so a linked image
            appears as a small "external image" label instead.
          </p>
          <p>
            Making something visible is deliberate; taking it back
            (unpublishing, removing a member, rotating a link) applies
            immediately. A visitor loses access within about a minute: public
            pages are cached briefly, and a page someone already had open
            re-checks on the same timer and empties itself when the answer comes
            back that it is gone. What anyone copied, screenshotted or saved
            before then is already theirs, and no amount of unpublishing reaches
            that.
          </p>
          <p>
            On a self-hosted instance, changing the server's signing secret
            invalidates every share link ever created, with no way to bring the
            old addresses back - you would need to create new links and send
            them out again. A public profile is unaffected.
          </p>
        </CardContent>
      </Card>

      <NewViewCard />

      {(views ?? []).map((view) => (
        <ViewCard
          key={view.id}
          view={view}
          grants={grantsByView.get(view.id) ?? []}
          safety={safetyCtx}
          systemId={system?.id ?? null}
        />
      ))}

      {audit && audit.entries.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Who can currently see what</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {suppressedNotice && (
              <p className="text-[11px] text-amber-600 dark:text-amber-500">
                {suppressedNotice}
              </p>
            )}
            {audit.entries.map((e) => (
              <div
                key={e.grant.id}
                className="rounded-md border px-3 py-2 text-sm"
              >
                <div className="flex items-center gap-2">
                  {e.grant.subject_type === "public" ? (
                    <Globe className="h-3.5 w-3.5" />
                  ) : (
                    <Link2 className="h-3.5 w-3.5" />
                  )}
                  <span className="font-medium">{e.view_name}</span>
                  <GrantStatusBadge grant={e.grant} />
                </div>
                {/* Reads as the list of what a visitor actually gets. With
                    the roster off the member count is not served at all, so
                    leading with it - or with anything hanging off it - would
                    describe a page nobody can see. The number here is the
                    SERVED one, like every other count on this line; the
                    curated total only appears when the two differ, as "3 of
                    5", because that gap is the thing worth knowing. */}
                <p className="mt-1 text-xs text-muted-foreground">
                  {e.include_members ? servedMembersLabel(e) : "no member list"}
                  {e.field_count > 0 && `, ${e.field_count} field${e.field_count === 1 ? "" : "s"}`}
                  {e.include_members && e.include_bio && ", bios"}
                  {e.include_fronting && ", live fronting"}
                  {e.include_members &&
                    e.include_relationships &&
                    `, ${e.relationship_count} relationship${e.relationship_count === 1 ? "" : "s"}`}
                  {e.include_groups &&
                    `, ${e.group_count} group${e.group_count === 1 ? "" : "s"}`}
                  {e.include_members && e.member_permalinks && ", member links"}
                </p>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </>
  );
}

/** Past its expiry the backend stops serving the grant, so "live" would lie. */
function isExpired(expiresAt: string): boolean {
  return new Date(expiresAt).getTime() <= Date.now();
}

function GrantStatusBadge({ grant }: { grant: ShareGrant }) {
  const { formatDate } = useDateFormatters();
  if (grant.status === "pending") {
    return (
      <Badge variant="outline" className="text-[10px]">
        goes live{grant.activates_at ? ` ${formatDate(grant.activates_at)}` : ""}
      </Badge>
    );
  }
  if (grant.expires_at) {
    if (isExpired(grant.expires_at)) {
      return (
        <Badge variant="outline" className="text-[10px] text-muted-foreground">
          expired {formatDate(grant.expires_at)}
        </Badge>
      );
    }
    return (
      <Badge variant="outline" className="text-[10px]">
        expires {formatDate(grant.expires_at)}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-[10px]">
      live
    </Badge>
  );
}

function NewViewCard() {
  const create = useCreateShareView();
  const [name, setName] = useState("");
  // Same defaults the server applies, spelled out here so the form shows the
  // truth about what it is about to create: a roster and nothing else.
  const [includeMembers, setIncludeMembers] = useState(true);
  const [includeBio, setIncludeBio] = useState(false);
  const [includeFronting, setIncludeFronting] = useState(false);
  const [frontingShowCount, setFrontingShowCount] = useState(true);
  const [includeRelationships, setIncludeRelationships] = useState(false);
  const [includeGroups, setIncludeGroups] = useState(false);
  const [memberPermalinks, setMemberPermalinks] = useState(false);

  function reset() {
    setName("");
    setIncludeMembers(true);
    setIncludeBio(false);
    setIncludeFronting(false);
    setFrontingShowCount(true);
    setIncludeRelationships(false);
    setIncludeGroups(false);
    setMemberPermalinks(false);
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    const data: ShareViewCreate = {
      name: name.trim(),
      include_members: includeMembers,
      include_bio: includeBio,
      include_fronting: includeFronting,
      fronting_show_count: frontingShowCount,
      include_relationships: includeRelationships,
      include_groups: includeGroups,
      member_permalinks: memberPermalinks,
    };
    create.mutate(data, { onSuccess: reset });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">New view</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="view-name">Name</Label>
            <Input
              id="view-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Public profile"
              required
            />
          </div>
          <div className="space-y-2">
            <Label className="text-xs uppercase tracking-wide text-muted-foreground">
              What this view shows
            </Label>
            <CheckboxRow
              checked={includeMembers}
              onChange={setIncludeMembers}
              label="Member list"
              desc="The members you add to it afterwards. Turning it off hides the roster, the bios, the relationships and the member pages. Fronting is on its own switch and is not affected: with fronting on, it still names the members it shows."
            />
            {!includeMembers && (
              <p className="ml-6 text-[11px] text-amber-600 dark:text-amber-500">
                The member list is off, so bios, relationships and permalinks
                are not shown to anyone, whatever they are set to here. Fronting
                is separate: leave it on and it still names whoever it shows.
              </p>
            )}
            <CheckboxRow
              checked={includeBio}
              onChange={setIncludeBio}
              label="Member bios"
              desc="Show each shown member's bio."
              disabled={!includeMembers}
              indent
            />
            <CheckboxRow
              checked={includeRelationships}
              onChange={setIncludeRelationships}
              label="Relationships between members"
              desc="Only relationships you marked public, and only where both members are shown here."
              disabled={!includeMembers}
              indent
            />
            <CheckboxRow
              checked={includeFronting}
              onChange={setIncludeFronting}
              label="Who's currently fronting"
              desc="Adds a live 'who is fronting now'. Front history is never shown."
            />
            {includeFronting && (
              <CheckboxRow
                checked={frontingShowCount}
                onChange={setFrontingShowCount}
                label="Count members fronting who aren't in this view"
                desc="Show them as an anonymous number rather than hiding them entirely."
                indent
              />
            )}
            <CheckboxRow
              checked={includeGroups}
              onChange={setIncludeGroups}
              label="Groups"
              desc="Show the groups themselves, and only those you set to Public in the group editor. Using a group to pick members does not show that group."
            />
          </div>
          <div className="space-y-2 border-t pt-4">
            <CheckboxRow
              checked={memberPermalinks}
              onChange={setMemberPermalinks}
              label="Member permalinks"
              desc="Give each member this view shows their own stable link, so you can point someone at one member. It reveals nothing the view does not already show, so it applies immediately - on and off alike."
              disabled={!includeMembers}
            />
          </div>
          <Button type="submit" disabled={create.isPending || !name.trim()}>
            {create.isPending ? "Creating..." : "Create view"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function ViewCard({
  view,
  grants,
  safety,
  systemId,
}: {
  view: ShareView;
  grants: ShareGrant[];
  safety: SafetyContext;
  systemId: string | null;
}) {
  const del = useDeleteShareView();
  const { formatDate } = useDateFormatters();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  // A flag turned on while the view was shared waits out the grace period, so
  // the card has to say so - the checkbox below still shows the live value.
  const flagsPending = EXPOSURE_FLAGS.some(
    (flag) => view[`pending_${flag}`] != null,
  );

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base flex items-center gap-2">
            {view.name}
            {view.is_shared && (
              <Badge className="text-[10px]">shared</Badge>
            )}
            {flagsPending && (
              <Badge
                variant="outline"
                className="text-[10px] border-amber-500/50 text-amber-600 dark:text-amber-500"
              >
                pending
                {view.flags_activate_at
                  ? ` - activates ${formatDate(view.flags_activate_at)}`
                  : ""}
              </Badge>
            )}
          </CardTitle>
          <div className="flex shrink-0 items-center gap-1">
            {/* Sits beside the view's own controls rather than down in the
                publish section on purpose: the question it answers ("what does
                this actually look like?") is one you ask while curating, not
                only at the moment of publishing - and it is the one control
                here that works exactly the same whether the view is published
                or not. */}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-muted-foreground"
              onClick={() => setPreviewing(true)}
            >
              <Eye className="h-3.5 w-3.5" />
              Preview as visitor
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-destructive hover:text-destructive"
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <ViewSettings view={view} safety={safety} />
        <ViewMembers view={view} safety={safety} />
        <ViewFields view={view} safety={safety} />
        <PublishSection view={view} grants={grants} safety={safety} systemId={systemId} />
      </CardContent>

      {previewing && (
        <PreviewDialog view={view} onClose={() => setPreviewing(false)} />
      )}

      {confirmDelete && (
        <Dialog open onOpenChange={(o) => !o && setConfirmDelete(false)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete view</DialogTitle>
              <DialogDescription>
                Delete &quot;{view.name}&quot;? This also revokes any links or
                public profile pointing at it. Immediate.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirmDelete(false)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                disabled={del.isPending}
                onClick={() =>
                  del.mutate(view.id, { onSuccess: () => setConfirmDelete(false) })
                }
              >
                {del.isPending ? "Deleting..." : "Delete view"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </Card>
  );
}

/**
 * The view, rendered as a visitor would receive it.
 *
 * Two things make this a preview rather than a mock-up, and both are load-
 * bearing enough to be worth stating where someone changing it will read them:
 *
 * - The payload comes from `/share-views/{id}/preview`, which the server builds
 *   with the SAME projection functions the anonymous endpoints use. Nothing here
 *   decides what a visitor can see; it only draws what the server said.
 * - It draws it with `PublicProfileBody` - the actual public page, minus its
 *   fetching - so a change to how the page looks changes the preview in the same
 *   commit, and the two cannot quietly diverge.
 *
 * Full-screen rather than a route under the app layout, for a reason that is the
 * whole point of the feature: inside `AppLayout` the page would render beside
 * the sidebar and inside the app's content column, so its width - and therefore
 * its responsive layout, its member cards, its tab strip - would be a
 * different page from the one a visitor gets. A preview that reflows differently
 * from the real thing is exactly the kind of quiet lie this is meant to prevent.
 * Covering the viewport gives the page the geometry it will actually have, and
 * costs only a route nobody needs to link to.
 */
function PreviewDialog({ view, onClose }: { view: ShareView; onClose: () => void }) {
  const { data, isLoading, isError } = useSharePreview(view.id, true);
  const suppressed = data?.suppressed
    ? suppressionNotice(data.suppressed)
    : null;

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        // The app's default dialog is a centred, rounded, max-w-2xl card. This
        // one is the viewport.
        className="top-0 left-0 block h-dvh w-screen max-h-none max-w-none translate-x-0 translate-y-0 gap-0 overflow-y-auto rounded-none border-0 p-0 sm:max-w-none lg:max-w-none"
        // Our own close control lives in the banner, where it cannot land on
        // top of the page's theme toggle.
        showCloseButton={false}
      >
        <DialogHeader className="sr-only">
          <DialogTitle>Preview of {view.name}</DialogTitle>
          <DialogDescription>
            This view as a visitor would see it. Nothing here is published by
            opening it.
          </DialogDescription>
        </DialogHeader>

        {isLoading && (
          <div className="flex h-dvh items-center justify-center">
            <p className="text-sm text-muted-foreground">Loading preview...</p>
          </div>
        )}

        {isError && (
          <div className="flex h-dvh items-center justify-center px-4">
            <div className="max-w-sm space-y-3 text-center">
              <p className="text-sm">The preview couldn&apos;t be loaded.</p>
              <Button variant="outline" onClick={onClose}>
                Close
              </Button>
            </div>
          </div>
        )}

        {data && (
          <PublicProfileBody
            system={data.system}
            members={data.members}
            fronting={data.fronting}
            relationships={data.relationships}
            groups={data.groups}
            // Never a link, even when the view publishes member permalinks: a
            // permalink is a real public URL, and clicking one from inside a
            // preview would walk the owner out of the preview and onto the live
            // page. Members open in place instead, which is what a visitor to a
            // permalink-less view gets - the one deliberate difference from the
            // real page, and it is about where a click goes rather than about
            // what is shown.
            linkTo={null}
            notice="This is a preview of what a visitor sees."
            banner={
              <div className="bg-amber-500/15 px-4 py-2 text-amber-900 dark:text-amber-200">
                <div className="mx-auto flex w-full max-w-2xl flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0 space-y-1">
                    <p className="text-xs font-medium">
                      Preview of &quot;{view.name}&quot; - only you can see this.
                      {view.member_permalinks &&
                        " Member links open here rather than going to the real page."}
                    </p>
                    {suppressed && (
                      <p className="text-[11px]">{suppressed}</p>
                    )}
                    {!suppressed && !view.is_shared && (
                      <p className="text-[11px]">
                        This view isn&apos;t published, so nobody can reach it
                        yet. This is what they would get if you did.
                      </p>
                    )}
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 shrink-0 text-xs"
                    onClick={onClose}
                  >
                    Close preview
                  </Button>
                </div>
              </div>
            }
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Why a checkbox that would show MORE is inert while the instance's public
 *  surface is off. Same split the API makes: a loosening is refused, a
 *  tightening never is. */
const LOOSEN_OFF_REASON =
  "Sharing is off on this instance, so a view can't be set to show more " +
  "while it stays off. Turning things off still works.";

function ViewSettings({ view, safety }: { view: ShareView; safety: SafetyContext }) {
  const off = useSharingOff();
  const update = useUpdateShareView();
  const [reauth, setReauth] = useState<null | { field: ExposureFlag; value: boolean }>(null);

  /** A box that is currently OFF would be a loosening to tick, which the API
   *  refuses while the instance's public surface is off; a box that is ON can
   *  always be unticked. Mirrored here rather than left to a bounced request,
   *  so the page never invites a click it knows will fail. */
  const lockedOn = (checked: boolean) => off && !checked;

  // Turning an option ON while the view is shared is a loosening; when the
  // safety category is armed it needs re-auth, whether or not a grace period
  // then stages it. Turning off is always immediate.
  function change(field: ExposureFlag, value: boolean) {
    const loosening = value && view.is_shared;
    if (loosening && safety.stepUp && safety.tier !== "none") {
      setReauth({ field, value });
      return;
    }
    // Sent bare, and re-prompted if the server asks for credentials anyway.
    // The gate above is a mirror of the server's own predicate, and a mirror
    // can drift; when it does, this is what keeps the bounce from being a dead
    // end with no field to type a password into.
    update.mutate(
      { id: view.id, data: { [field]: value }, skipErrorToast: true },
      {
        onError: (err) => {
          if (isStepUpRequiredError(err)) {
            setReauth({ field, value });
            return;
          }
          showApiErrorToast(err, "Couldn't update this view.", { force: true });
        },
      },
    );
  }

  /** Member permalinks bypass `change()` on purpose: nothing is exposed that
   *  the roster does not already expose, so there is nothing to stage and
   *  nothing to step up for, in either direction. */
  function changePermalinks(value: boolean) {
    update.mutate({ id: view.id, data: { member_permalinks: value } });
  }

  // With the roster off the view serves nothing member-shaped, so the options
  // that decorate a member have nothing to attach to. They keep their stored
  // value - turning the roster back on should not silently forget them - but
  // they are shown as inert rather than pretending to do something.
  const membersOff = !view.include_members;

  return (
    <div className="space-y-3">
      <Label className="text-xs uppercase tracking-wide text-muted-foreground">
        What this view shows
      </Label>
      <div className="space-y-2">
        <CheckboxRow
          checked={view.include_members}
          onChange={(v) => change("include_members", v)}
          label="Member list"
          desc="The members listed below. Turning it off hides the roster, the bios, the relationships and the member pages. Fronting is on its own switch and is not affected: with fronting on, it still names the members it shows."
          disabled={lockedOn(view.include_members)}
          title={lockedOn(view.include_members) ? LOOSEN_OFF_REASON : undefined}
        />
        {membersOff && (
          <p className="ml-6 text-[11px] text-amber-600 dark:text-amber-500">
            The member list is off, so bios, relationships and permalinks are
            not shown to anyone, whatever they are set to here. Fronting is
            separate: leave it on and it still names whoever it shows.
          </p>
        )}
        <CheckboxRow
          checked={view.include_bio}
          onChange={(v) => change("include_bio", v)}
          label="Member bios"
          disabled={membersOff || lockedOn(view.include_bio)}
          title={lockedOn(view.include_bio) ? LOOSEN_OFF_REASON : undefined}
          indent
        />
        <CheckboxRow
          checked={view.include_relationships}
          onChange={(v) => change("include_relationships", v)}
          label="Relationships between members"
          desc="Only relationships you marked public show, and only where both members are shown in this view."
          disabled={membersOff || lockedOn(view.include_relationships)}
          title={
            lockedOn(view.include_relationships) ? LOOSEN_OFF_REASON : undefined
          }
          indent
        />
        <CheckboxRow
          checked={view.include_fronting}
          onChange={(v) => change("include_fronting", v)}
          label="Who's currently fronting"
          desc="A live 'who is fronting now'. Front history is never shown."
          disabled={lockedOn(view.include_fronting)}
          title={lockedOn(view.include_fronting) ? LOOSEN_OFF_REASON : undefined}
        />
        {view.include_fronting && (
          <CheckboxRow
            checked={view.fronting_show_count}
            onChange={(v) => change("fronting_show_count", v)}
            label="Count fronting members not in this view"
            desc="Show them as an anonymous number rather than hiding them entirely."
            disabled={lockedOn(view.fronting_show_count)}
            title={
              lockedOn(view.fronting_show_count) ? LOOSEN_OFF_REASON : undefined
            }
            indent
          />
        )}
        <CheckboxRow
          checked={view.include_groups}
          onChange={(v) => change("include_groups", v)}
          label="Groups"
          desc="Show the groups themselves, and only those you set to Public in the group editor. Using a group below to pick members does not show that group."
          disabled={lockedOn(view.include_groups)}
          title={lockedOn(view.include_groups) ? LOOSEN_OFF_REASON : undefined}
        />
      </div>

      {/* Outside the block above, because it is the one display option here
          that neither stages nor exposes: it addresses what is already shown. */}
      <div className="space-y-2 border-t pt-3">
        <CheckboxRow
          checked={view.member_permalinks}
          onChange={changePermalinks}
          label="Member permalinks"
          desc="Give each member this view already shows their own stable link, so you can point someone at one member. It reveals nothing the view does not already show, so it applies immediately - turning it on and turning it off both take effect at once."
          disabled={membersOff}
        />
      </div>
      {reauth && (
        <DestructiveConfirmDialog
          open
          onOpenChange={(o) => !o && setReauth(null)}
          title="Confirm change"
          description={`This exposes more on an already-shared view, so it needs confirmation. ${effectSentence(safety)}`}
          tier={safety.tier}
          actionLabel="Confirm"
          actionLabelLoading="Saving..."
          loading={update.isPending}
          onConfirm={(c?: DestructiveConfirm) =>
            update.mutate(
              { id: view.id, data: { [reauth.field]: reauth.value, ...c } },
              { onSuccess: () => setReauth(null) },
            )
          }
        />
      )}
    </div>
  );
}

function ViewMembers({ view, safety }: { view: ShareView; safety: SafetyContext }) {
  const { data: members } = useMembers();
  const { data: groups } = useGroups();
  const { formatDate } = useDateFormatters();
  const addMember = useAddViewMember();
  const removeMember = useRemoveViewMember();
  const addGroup = useAddViewGroup();
  const [pendingAdd, setPendingAdd] = useState<string | null>(null);
  const [pendingGroupAdd, setPendingGroupAdd] = useState<string | null>(null);
  const [groupToRemove, setGroupToRemove] = useState<ShareViewGroupRow | null>(
    null,
  );

  const groupById = useMemo(() => {
    const m = new Map<string, string>();
    for (const g of groups ?? []) m.set(g.id, g.name);
    return m;
  }, [groups]);

  const memberById = useMemo(() => {
    const m = new Map<string, string>();
    for (const mem of members ?? [])
      m.set(mem.id, mem.display_name || mem.name);
    return m;
  }, [members]);

  // A custom front ("Asleep", "Away") is a Member row like any other, so both
  // the chips and the picker used to show it unlabelled next to the people -
  // and a custom front in a view with fronting on publishes a status, which is
  // a different thing to publish than a name. Marked in both places.
  const customFrontIds = useMemo(() => {
    const s = new Set<string>();
    for (const mem of members ?? []) if (mem.is_custom_front) s.add(mem.id);
    return s;
  }, [members]);

  const inView = new Set(view.members.map((m) => m.member_id));
  const addable = (members ?? []).filter(
    (m) => !inView.has(m.id) && !m.never_shareable,
  );
  // Sectioned rather than toggled: the Select already renders labelled groups
  // elsewhere (see timezone-select), so a divider is the smaller change and
  // costs the picker no extra state.
  const addableMembers = addable.filter((m) => !m.is_custom_front);
  const addableFronts = addable.filter((m) => m.is_custom_front);
  // Members that are in the view but won't actually appear. Taken from the
  // server's own answer rather than re-derived from member privacy here: the
  // client's version of this test missed an archived member and one queued for
  // deletion, both of which leave the public page at once, so a view could be
  // showing fewer people than this screen implied. Surfaced so "why isn't X
  // showing?" never becomes a mystery.
  const hiddenInView = view.members.filter(
    (row) => notServedCopy(row) !== null,
  ).length;

  function doAdd(memberId: string, reauth?: DestructiveConfirm) {
    addMember.mutate({ viewId: view.id, memberId, reauth });
  }

  function onPick(memberId: string) {
    if (view.is_shared && safety.stepUp && safety.tier !== "none") {
      setPendingAdd(memberId);
      return;
    }
    // Bare first, re-prompt if the server disagrees about whether this needed
    // credentials - see the same fallback on the exposure flags above.
    addMember.mutate(
      { viewId: view.id, memberId, skipErrorToast: true },
      {
        onError: (err) => {
          if (isStepUpRequiredError(err)) {
            setPendingAdd(memberId);
            return;
          }
          showApiErrorToast(err, "Couldn't add that member to the view.", {
            force: true,
          });
        },
      },
    );
  }

  function doAddGroup(groupId: string, reauth?: DestructiveConfirm) {
    addGroup.mutate({ viewId: view.id, groupId, reauth });
  }

  // Same gate as adding a member one at a time: a group add on a shared view
  // exposes people, so it needs the step-up rather than a bounced request.
  function onPickGroup(groupId: string) {
    if (view.is_shared && safety.stepUp && safety.tier !== "none") {
      setPendingGroupAdd(groupId);
      return;
    }
    addGroup.mutate(
      { viewId: view.id, groupId, skipErrorToast: true },
      {
        onError: (err) => {
          if (isStepUpRequiredError(err)) {
            setPendingGroupAdd(groupId);
            return;
          }
          showApiErrorToast(err, "Couldn't add that group to the view.", {
            force: true,
          });
        },
      },
    );
  }

  return (
    <div className="space-y-2">
      <Label className="text-xs uppercase tracking-wide text-muted-foreground">
        Members
      </Label>
      {view.members.length === 0 ? (
        <p className="text-sm text-muted-foreground">No members yet.</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {view.members.map((row) => {
            const label = memberById.get(row.member_id);
            const notServed = notServedCopy(row);
            return (
              <Badge
                key={row.id}
                variant={notServed ? "outline" : "secondary"}
                className={`gap-1 ${notServed ? "text-muted-foreground" : ""}`}
                title={notServed?.title}
              >
                {label ?? "member"}
                {customFrontIds.has(row.member_id) && (
                  <span
                    className="text-[9px] opacity-70"
                    title="A custom front - a status like Asleep or Away rather than a person. Its live state shows on this page only if its 'keep fronting private' setting is off, which for a custom front is on by default."
                  >
                    custom front
                  </span>
                )}
                {notServed && (
                  <span className="text-[9px]">{notServed.badge}</span>
                )}
                {/* Unchanged, and deliberately separate from the badge above:
                    a member can be both waiting out a grace window AND held
                    back by something that will still be true afterwards, and
                    the owner needs to be told both. */}
                {row.status === "pending" && (
                  <span className="text-[9px] opacity-70">pending</span>
                )}
                <button
                  type="button"
                  className="ml-0.5 hover:text-destructive"
                  onClick={() => removeMember.mutate({ viewId: view.id, memberId: row.member_id })}
                  aria-label="Remove"
                >
                  ×
                </button>
              </Badge>
            );
          })}
        </div>
      )}
      {hiddenInView > 0 && (
        <p className="text-[11px] text-amber-600 dark:text-amber-500">
          {hiddenInView} member{hiddenInView === 1 ? "" : "s"} here won't
          appear on the page. Each one says why: a member shows only while
          their privacy is Public, they aren't marked never shareable, and they
          are neither archived nor queued for deletion.
        </p>
      )}
      {view.groups.length > 0 && (
        <div className="space-y-1">
          {view.groups.map((row) => (
            <div
              key={row.id}
              className="flex items-center justify-between gap-2 rounded-md border px-2 py-1 text-[11px] text-muted-foreground"
            >
              <span className="min-w-0 truncate">
                Added from group{" "}
                <span className="font-medium text-foreground">
                  {groupById.get(row.group_id) ?? "a deleted group"}
                </span>{" "}
                on {formatDate(row.synced_at)}
              </span>
              <button
                type="button"
                className="shrink-0 hover:text-destructive"
                onClick={() => setGroupToRemove(row)}
                aria-label="Remove group"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <Select value="" onValueChange={onPick}>
          <SelectTrigger className="h-8 text-xs">
            <SelectValue placeholder="Add a member..." />
          </SelectTrigger>
          <SelectContent>
            {addable.length === 0 ? (
              <div className="px-2 py-1.5 text-xs text-muted-foreground">
                No more members to add
              </div>
            ) : (
              <>
                {addableMembers.length > 0 && (
                  <SelectGroup>
                    <SelectLabel>Members</SelectLabel>
                    {addableMembers.map((m) => (
                      <SelectItem key={m.id} value={m.id}>
                        {m.display_name || m.name}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                )}
                {addableFronts.length > 0 && (
                  <SelectGroup>
                    <SelectLabel>Custom fronts</SelectLabel>
                    {addableFronts.map((m) => (
                      <SelectItem key={m.id} value={m.id}>
                        {m.display_name || m.name}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                )}
              </>
            )}
          </SelectContent>
        </Select>
        <Select value="" onValueChange={onPickGroup}>
          <SelectTrigger className="h-8 text-xs">
            <SelectValue placeholder="Add from group..." />
          </SelectTrigger>
          <SelectContent>
            {(groups ?? []).length === 0 ? (
              <div className="px-2 py-1.5 text-xs text-muted-foreground">
                No groups
              </div>
            ) : (
              (groups ?? []).map((g) => (
                <SelectItem key={g.id} value={g.id}>
                  {g.name}
                </SelectItem>
              ))
            )}
          </SelectContent>
        </Select>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Adding a group brings in its current members as a one-time pick; adding
        someone to the group later never publishes them automatically.
      </p>
      {pendingAdd && (
        <DestructiveConfirmDialog
          open
          onOpenChange={(o) => !o && setPendingAdd(null)}
          title="Add member to a shared view"
          description={`This view is already published, so adding a member exposes them. ${effectSentence(safety)} Confirm to continue.`}
          tier={safety.tier}
          actionLabel="Add"
          actionLabelLoading="Adding..."
          loading={addMember.isPending}
          onConfirm={(c?: DestructiveConfirm) => {
            doAdd(pendingAdd, c);
            setPendingAdd(null);
          }}
        />
      )}
      {pendingGroupAdd && (
        <DestructiveConfirmDialog
          open
          onOpenChange={(o) => !o && setPendingGroupAdd(null)}
          title="Add a group to a shared view"
          description={`This view is already published, so adding this group's current members exposes them. ${effectSentence(safety)} Confirm to continue.`}
          tier={safety.tier}
          actionLabel="Add"
          actionLabelLoading="Adding..."
          loading={addGroup.isPending}
          onConfirm={(c?: DestructiveConfirm) => {
            doAddGroup(pendingGroupAdd, c);
            setPendingGroupAdd(null);
          }}
        />
      )}
      {groupToRemove && (
        <RemoveGroupDialog
          viewId={view.id}
          groupId={groupToRemove.group_id}
          groupName={groupById.get(groupToRemove.group_id) ?? "this group"}
          onClose={() => setGroupToRemove(null)}
        />
      )}
    </div>
  );
}

function RemoveGroupDialog({
  viewId,
  groupId,
  groupName,
  onClose,
}: {
  viewId: string;
  groupId: string;
  groupName: string;
  onClose: () => void;
}) {
  const removeGroup = useRemoveViewGroup();
  // Matches the API default: the usual reason to drop a group is that those
  // people shouldn't be in the view at all.
  const [removeMembers, setRemoveMembers] = useState(true);

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Remove group</DialogTitle>
          <DialogDescription>
            Drop the link between this view and &quot;{groupName}&quot;. The
            group itself is not changed.
          </DialogDescription>
        </DialogHeader>
        {/* Says "originally added" rather than "its members" on purpose: the
            server removes the rows this group's expansion created, not whoever
            is in the group today. Those differ once anyone joins or leaves it,
            and the honest sentence is the one that matches what happens. */}
        <CheckboxRow
          checked={removeMembers}
          onChange={setRemoveMembers}
          label="Also remove the members this group originally added"
          desc={
            "Only the members this group put in the view. Anyone you added by " +
            "hand, or who another group brought in, stays. Uncheck to keep " +
            "everyone, as individually picked members."
          }
        />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={removeGroup.isPending}
            onClick={() =>
              removeGroup.mutate(
                { viewId, groupId, removeMembers },
                { onSuccess: onClose },
              )
            }
          >
            {removeGroup.isPending ? "Removing..." : "Remove"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ViewFields({ view, safety }: { view: ShareView; safety: SafetyContext }) {
  const { data: fields } = useCustomFields();
  const addField = useAddViewField();
  const removeField = useRemoveViewField();
  const [pendingAdd, setPendingAdd] = useState<string | null>(null);

  const fieldById = useMemo(() => {
    const m = new Map<string, { label: string; isPublic: boolean }>();
    for (const f of fields ?? [])
      m.set(f.id, { label: f.name, isPublic: f.privacy === "public" });
    return m;
  }, [fields]);

  const inView = new Set(view.fields.map((f) => f.field_id));
  const addable = (fields ?? []).filter((f) => !inView.has(f.id));
  // Fields that are in the view but won't actually appear, because the
  // definition's own privacy keeps it off the public tier. Surfaced for the
  // same reason the member version above is: "why isn't X showing?" must never
  // become a mystery.
  const hiddenInView = view.fields.filter(
    (row) => fieldById.get(row.field_id)?.isPublic === false,
  ).length;

  function doAdd(fieldId: string, reauth?: DestructiveConfirm) {
    addField.mutate({ viewId: view.id, fieldId, reauth });
  }

  function onPick(fieldId: string) {
    if (view.is_shared && safety.stepUp && safety.tier !== "none") {
      setPendingAdd(fieldId);
      return;
    }
    addField.mutate(
      { viewId: view.id, fieldId, skipErrorToast: true },
      {
        onError: (err) => {
          if (isStepUpRequiredError(err)) {
            setPendingAdd(fieldId);
            return;
          }
          showApiErrorToast(err, "Couldn't add that field to the view.", {
            force: true,
          });
        },
      },
    );
  }

  if ((fields ?? []).length === 0) return null;

  return (
    <div className="space-y-2">
      <Label className="text-xs uppercase tracking-wide text-muted-foreground">
        Custom fields shown
      </Label>
      {view.fields.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {view.fields.map((row) => {
            const info = fieldById.get(row.field_id);
            const hidden = info?.isPublic === false;
            return (
              <Badge
                key={row.id}
                variant={hidden ? "outline" : "secondary"}
                className={`gap-1 ${hidden ? "text-muted-foreground" : ""}`}
                title={
                  hidden
                    ? "This field's privacy isn't Public, so it won't appear. Set its privacy to Public in Settings to show it."
                    : undefined
                }
              >
                {info?.label ?? "field"}
                {hidden && <span className="text-[9px]">won't show</span>}
                {row.status === "pending" && (
                  <span className="text-[9px] opacity-70">pending</span>
                )}
                <button
                  type="button"
                  className="ml-0.5 hover:text-destructive"
                  onClick={() => removeField.mutate({ viewId: view.id, fieldId: row.field_id })}
                  aria-label="Remove"
                >
                  ×
                </button>
              </Badge>
            );
          })}
        </div>
      )}
      {hiddenInView > 0 && (
        <p className="text-[11px] text-amber-600 dark:text-amber-500">
          {hiddenInView} field{hiddenInView === 1 ? "" : "s"} here won't appear:
          only fields whose privacy is Public show on a public profile or link.
          Change it under Settings - custom fields. The level applies to that
          field on every member.
        </p>
      )}
      <Select value="" onValueChange={onPick}>
        <SelectTrigger className="h-8 text-xs w-full">
          <SelectValue placeholder="Expose a custom field..." />
        </SelectTrigger>
        <SelectContent>
          {addable.length === 0 ? (
            <div className="px-2 py-1.5 text-xs text-muted-foreground">
              No more fields to add
            </div>
          ) : (
            addable.map((f) => (
              <SelectItem key={f.id} value={f.id}>
                {f.name}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
      {pendingAdd && (
        <DestructiveConfirmDialog
          open
          onOpenChange={(o) => !o && setPendingAdd(null)}
          title="Expose a field on a shared view"
          description={`This view is already published, so exposing a field shows its value on everyone the view shows. ${effectSentence(safety)} Confirm to continue.`}
          tier={safety.tier}
          actionLabel="Expose"
          actionLabelLoading="Adding..."
          loading={addField.isPending}
          onConfirm={(c?: DestructiveConfirm) => {
            doAdd(pendingAdd, c);
            setPendingAdd(null);
          }}
        />
      )}
    </div>
  );
}

/** One publish control, which explains itself when the instance's public
 *  surface is off. The title sits on a wrapper rather than on the button
 *  because a disabled button receives no mouse events in several browsers, so
 *  a tooltip on the button itself would simply never appear. */
function PublishButton({
  off,
  icon: Icon,
  label,
  onClick,
}: {
  off: boolean;
  icon: typeof Globe;
  label: string;
  onClick: () => void;
}) {
  return (
    <span
      className="inline-flex"
      title={
        off
          ? "Sharing is off on this instance, so nothing new can be published " +
            "until the operator turns it back on."
          : undefined
      }
    >
      <Button
        variant="outline"
        size="sm"
        className="h-8 text-xs"
        disabled={off}
        onClick={onClick}
      >
        <Icon className="mr-1 h-3.5 w-3.5" /> {label}
      </Button>
    </span>
  );
}

function PublishSection({
  view,
  grants,
  safety,
  systemId,
}: {
  view: ShareView;
  grants: ShareGrant[];
  safety: SafetyContext;
  systemId: string | null;
}) {
  const off = useSharingOff();
  const { user } = useAuth();
  const attest = useAttestAdult();
  const createGrant = useCreateShareGrant();
  const rotate = useRotateShareGrant();
  const revoke = useRevokeShareGrant();

  const [publishing, setPublishing] = useState<null | "public" | "link">(null);
  // Publishing can fail for a reason only the server knows and only the owner
  // can fix (system privacy still private), and the auto-toast flattens a 400
  // to "Invalid request." Keep the server's own wording in the dialog.
  const [publishError, setPublishError] = useState<string | null>(null);
  // Set when the server bounces a publish asking for credentials the dialog did
  // not offer. The client's own gate mirrors the server's; if the mirror ever
  // drifts, this is what turns a dead end into a second attempt.
  const [publishStepUp, setPublishStepUp] = useState(false);
  const [tokenShown, setTokenShown] = useState<ShareGrantCreated | null>(null);
  const [confirmRotate, setConfirmRotate] = useState<ShareGrant | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<ShareGrant | null>(null);

  const hasPublic = grants.some((g) => g.subject_type === "public");
  const origin = typeof window !== "undefined" ? window.location.origin : "";

  function beginPublish(kind: "public" | "link") {
    setPublishError(null);
    setPublishStepUp(false);
    setPublishing(kind);
  }

  function grantUrl(g: ShareGrant, token?: string | null): string {
    if (g.subject_type === "public") return `${origin}/p/${systemId ?? ""}`;
    return token ? `${origin}/s/${token}` : `${origin}/s/…`;
  }

  return (
    <div className="space-y-3 border-t pt-3">
      <Label className="text-xs uppercase tracking-wide text-muted-foreground">
        Publish
      </Label>

      {grants.length > 0 && (
        <div className="space-y-2">
          {grants.map((g) => (
            <div key={g.id} className="rounded-md border px-3 py-2 text-sm">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  {g.subject_type === "public" ? (
                    <Globe className="h-3.5 w-3.5 shrink-0" />
                  ) : (
                    <Link2 className="h-3.5 w-3.5 shrink-0" />
                  )}
                  <span className="font-medium">
                    {g.subject_type === "public" ? "Public profile" : "Share link"}
                  </span>
                  <GrantStatusBadge grant={g} />
                </div>
                <div className="flex shrink-0 gap-1">
                  {g.subject_type === "link" && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 text-xs"
                      disabled={rotate.isPending}
                      onClick={() => setConfirmRotate(g)}
                    >
                      Rotate
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs text-destructive hover:text-destructive"
                    disabled={revoke.isPending}
                    onClick={() => setConfirmRevoke(g)}
                  >
                    Unpublish
                  </Button>
                </div>
              </div>
              {g.subject_type === "public" &&
                (systemId ? (
                  <CopyableUrl url={grantUrl(g)} />
                ) : (
                  // The public URL is built from the system id, so don't offer
                  // a half-formed link to copy while that is still loading.
                  <Skeleton className="mt-1 h-7 w-full" />
                ))}
              {g.subject_type === "link" && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  The link is shown once when created or rotated. Rotate to
                  invalidate a link that spread too far.
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {!hasPublic && (
          <PublishButton
            off={off}
            icon={Globe}
            label="Make public"
            onClick={() => beginPublish("public")}
          />
        )}
        <PublishButton
          off={off}
          icon={Link2}
          label="Create share link"
          onClick={() => beginPublish("link")}
        />
      </div>
      {off && (
        <p className="text-[11px] text-muted-foreground">
          Publishing is off on this instance. Anything already published above is
          kept and is serving nobody for now; unpublishing and rotating still
          work, and are the only way to make sure it stays that way.
        </p>
      )}

      {publishing && (
        <PublishDialog
          kind={publishing}
          needsAttestation={!!user && user.adult_attested_at === null}
          safety={safety}
          demanded={publishStepUp}
          onCancel={() => {
            setPublishError(null);
            setPublishStepUp(false);
            setPublishing(null);
          }}
          busy={attest.isPending || createGrant.isPending}
          error={publishError}
          onConfirm={async (creds) => {
            setPublishError(null);
            if (user && user.adult_attested_at === null) {
              await attest.mutateAsync();
            }
            createGrant.mutate(
              {
                view_id: view.id,
                subject_type: publishing,
                ...creds,
              },
              {
                onSuccess: (res) => {
                  setPublishing(null);
                  if (res.token) setTokenShown(res);
                  else toast.success("Published");
                },
                // The dialog stays open so the owner can act on this without
                // rebuilding their input - the re-auth failure path too.
                onError: (err) => {
                  // A bounce asking for credentials is the one failure the
                  // dialog can fix itself: turn the fields on and let the owner
                  // try again in place.
                  if (isStepUpRequiredError(err)) setPublishStepUp(true);
                  // preferDetail: publishing fails for reasons written for the
                  // owner about their own settings, and the generic summary for
                  // a 400 ("Invalid request.") throws away the one sentence
                  // that says what to change.
                  setPublishError(
                    apiErrorMessage(err, "Couldn't publish.", {
                      preferDetail: true,
                    }),
                  );
                },
              },
            );
          }}
        />
      )}

      {confirmRotate && (
        <Dialog open onOpenChange={(o) => !o && setConfirmRotate(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Rotate share link</DialogTitle>
              <DialogDescription>
                Issue a new link for this view? The existing link stops working
                immediately and cannot be shown again, so anyone still using it
                needs the replacement. The new link is shown once.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirmRotate(null)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                disabled={rotate.isPending}
                onClick={() =>
                  rotate.mutate(confirmRotate.id, {
                    onSuccess: (res) => {
                      setConfirmRotate(null);
                      setTokenShown(res);
                    },
                  })
                }
              >
                {rotate.isPending ? "Rotating..." : "Rotate link"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      {confirmRevoke && (
        <Dialog open onOpenChange={(o) => !o && setConfirmRevoke(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {confirmRevoke.subject_type === "public"
                  ? "Unpublish the public profile"
                  : "Unpublish this share link"}
              </DialogTitle>
              <DialogDescription>
                {confirmRevoke.subject_type === "public"
                  ? "The public profile goes dark immediately. "
                  : "The link stops working immediately. "}
                A page a visitor already loaded can linger for up to a minute of
                caching. This cannot be undone: republishing creates a new
                {confirmRevoke.subject_type === "public" ? " grant" : " link"}.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirmRevoke(null)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                disabled={revoke.isPending}
                onClick={() =>
                  revoke.mutate(confirmRevoke.id, {
                    onSuccess: () => setConfirmRevoke(null),
                  })
                }
              >
                {revoke.isPending ? "Unpublishing..." : "Unpublish"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      {tokenShown?.token && (
        <TokenDialog
          url={`${origin}/s/${tokenShown.token}`}
          onClose={() => setTokenShown(null)}
        />
      )}
    </div>
  );
}

function PublishDialog({
  kind,
  needsAttestation,
  safety,
  demanded,
  onConfirm,
  onCancel,
  busy,
  error,
}: {
  kind: "public" | "link";
  needsAttestation: boolean;
  safety: SafetyContext;
  /** The server bounced a publish asking for step-up credentials this dialog
   *  did not think were needed. Shows the fields regardless of `safety`. */
  demanded: boolean;
  onConfirm: (creds: { password?: string; totp_code?: string }) => void;
  onCancel: () => void;
  busy: boolean;
  error?: string | null;
}) {
  const { user } = useAuth();
  const [attested, setAttested] = useState(false);
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");

  // `stepUp`, not `staged`: publishing re-auths whenever the category is armed,
  // whether or not a grace window then holds the grant back. `demanded` is the
  // belt-and-braces half - if the server asks for a credential this dialog did
  // not offer, the field appears rather than the owner being left reading
  // "Password required" with nowhere to type one.
  const armed = safety.stepUp || demanded;
  // Same TOTP-not-enrolled fallback the shared DestructiveConfirmDialog makes,
  // for the same reason and against the same server-side fail-safe.
  const totpByTier = safety.tier === "totp" || safety.tier === "both";
  const needsTotp = armed && totpByTier && !!user?.totp_enabled;
  const needsPassword =
    armed &&
    (safety.tier === "password" ||
      safety.tier === "both" ||
      (totpByTier && !user?.totp_enabled));

  const disabled =
    busy ||
    (needsAttestation && !attested) ||
    (needsPassword && !password) ||
    (needsTotp && !totp);

  return (
    <Dialog open onOpenChange={(o) => !o && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {kind === "public" ? "Make public" : "Create share link"}
          </DialogTitle>
          <DialogDescription>
            {kind === "public"
              ? "This view becomes reachable to anyone with your system link."
              : "This creates an opaque, revocable link to this view."}
            {safety.staged && " It takes effect after your grace period."}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {needsAttestation && (
            <label className="flex items-start gap-3 cursor-pointer">
              <Checkbox
                checked={attested}
                onCheckedChange={(v) => setAttested(v === true)}
                className="mt-0.5"
              />
              <span className="text-sm">
                I confirm I am 18 or older. (Recorded as a yes/no only; no date
                of birth or ID is collected or stored.)
              </span>
            </label>
          )}
          {needsPassword && (
            <div className="space-y-1">
              <Label htmlFor="publish-password" className="text-sm">Password</Label>
              <Input
                id="publish-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
              />
            </div>
          )}
          {needsTotp && (
            <div className="space-y-1">
              <Label htmlFor="publish-totp" className="text-sm">TOTP code</Label>
              <Input
                id="publish-totp"
                value={totp}
                onChange={(e) => setTotp(e.target.value)}
                placeholder="6-digit code"
                inputMode="numeric"
                maxLength={6}
                autoComplete="off"
              />
            </div>
          )}
        </div>
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            disabled={disabled}
            onClick={() =>
              onConfirm({
                password: needsPassword ? password : undefined,
                totp_code: needsTotp ? totp : undefined,
              })
            }
          >
            {busy ? "Publishing..." : kind === "public" ? "Make public" : "Create link"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Shown once, and only once: the raw token never comes back from the server.
 *  Closing takes a deliberate acknowledgement - no stray Escape, no click on
 *  the overlay, no corner X - because a mis-dismiss means rotating the link. */
function TokenDialog({ url, onClose }: { url: string; onClose: () => void }) {
  return (
    <Dialog open>
      <DialogContent
        showCloseButton={false}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>Your share link</DialogTitle>
          <DialogDescription>
            Copy it now - it is shown only once, and closing this is the last
            chance. Anyone with this link can see the view until you rotate or
            unpublish it.
          </DialogDescription>
        </DialogHeader>
        <CopyableUrl url={url} big />
        <DialogFooter>
          <Button onClick={onClose}>I've saved this link</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CopyableUrl({ url, big }: { url: string; big?: boolean }) {
  // Manual fallback for anywhere the clipboard API is unavailable: one click
  // selects the whole link so it can be copied by hand.
  function selectAll(e: MouseEvent<HTMLElement>) {
    const range = document.createRange();
    range.selectNodeContents(e.currentTarget);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }

  function copy() {
    // navigator.clipboard is undefined outside a secure context - a plain-http
    // selfhost, say - so this can't assume the write happened.
    if (!navigator.clipboard) {
      toast.error("Couldn't copy - select the link and copy it manually");
      return;
    }
    navigator.clipboard.writeText(url).then(
      () => toast.success("Copied"),
      () => toast.error("Couldn't copy - select the link and copy it manually"),
    );
  }

  return (
    <div className={`mt-1 flex items-center gap-1 ${big ? "" : "text-xs"}`}>
      <code
        onClick={selectAll}
        title="Click to select the whole link"
        className={`min-w-0 flex-1 cursor-text rounded bg-muted px-2 py-1 text-xs ${big ? "break-all" : "truncate"}`}
      >
        {url}
      </code>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 px-2"
        onClick={copy}
      >
        <Copy className="h-3.5 w-3.5" />
      </Button>
      <a href={url} target="_blank" rel="noreferrer">
        <Button variant="ghost" size="sm" className="h-7 px-2">
          <ExternalLink className="h-3.5 w-3.5" />
        </Button>
      </a>
    </div>
  );
}

function CheckboxRow({
  checked,
  onChange,
  label,
  desc,
  indent,
  disabled,
  title,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  desc?: string;
  indent?: boolean;
  /** For an option that depends on another one that is off: the stored value
   *  still shows, so nothing is forgotten, but the row reads as inert rather
   *  than as a promise the view can't keep. */
  disabled?: boolean;
  /** Why this row is inert, when that isn't obvious from the row above it.
   *  On the label rather than the checkbox, because a disabled input doesn't
   *  receive the mouse events a tooltip needs in every browser. */
  title?: string;
}) {
  return (
    <label
      title={title}
      className={`flex items-start gap-3 ${disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer"} ${indent ? "ml-6" : ""}`}
    >
      <Checkbox
        checked={checked}
        onCheckedChange={(v) => onChange(v === true)}
        disabled={disabled}
        className="mt-0.5"
      />
      <div>
        <span className="text-sm font-medium">{label}</span>
        {desc && <p className="text-xs text-muted-foreground mt-0.5">{desc}</p>}
      </div>
    </label>
  );
}
