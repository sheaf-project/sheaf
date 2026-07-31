import { type FormEvent, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy, ExternalLink, Globe, Link2, Trash2 } from "lucide-react";

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
  useAddViewField,
  useRemoveViewField,
  useCreateShareGrant,
  useRotateShareGrant,
  useRevokeShareGrant,
  useAttestAdult,
} from "@/hooks/use-sharing";
import { useMembers } from "@/hooks/use-members";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { useGroups } from "@/hooks/use-groups";
import { useCustomFields } from "@/hooks/use-custom-fields";
import { useAuth } from "@/hooks/use-auth";
import { getSystemSafety } from "@/lib/system-safety";
import { getMySystem } from "@/lib/systems";
import type {
  ShareGrant,
  ShareGrantCreated,
  ShareView,
  ShareViewCreate,
} from "@/types/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
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
import type { DeleteConfirmation, DestructiveConfirm } from "@/types/api";

/** The instance-level gate: hide the whole surface when the operator hasn't
 *  enabled public profiles. Also serves as an honest "off" explainer. */
export function SettingsSharingPage() {
  const { user } = useAuth();
  if (user && !user.public_profiles_enabled) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Sharing</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Public profiles and share links are turned off on this instance.
            The server operator can enable them by setting
            {" "}
            <code className="rounded bg-muted px-1 py-0.5 text-xs">
              PUBLIC_PROFILES_ENABLED=true
            </code>
            .
          </p>
        </CardContent>
      </Card>
    );
  }
  return <SharingManager />;
}

// Re-auth context shared by every exposing action on the page.
interface SafetyContext {
  safeguarded: boolean;
  tier: DeleteConfirmation;
}

function SharingManager() {
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
    return {
      safeguarded: !!s && s.grace_period_days > 0 && s.applies_to_profile_visibility,
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

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Sharing</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>
            A <strong>view</strong> is a curated selection of exactly which
            members and custom fields are shown. A view is visible to no one
            until you publish it, either as a public profile or behind a
            revocable link. Nothing is ever shown that you did not add to a
            view.
          </p>
          <p>
            Making something visible is deliberate; taking it back
            (unpublishing, removing a member, rotating a link) is always
            immediate.
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
                <p className="mt-1 text-xs text-muted-foreground">
                  {e.member_count} member{e.member_count === 1 ? "" : "s"}
                  {e.field_count > 0 && `, ${e.field_count} field${e.field_count === 1 ? "" : "s"}`}
                  {e.include_bio && ", bios"}
                  {e.include_fronting && ", live fronting"}
                </p>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </>
  );
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
  const [includeBio, setIncludeBio] = useState(false);
  const [includeFronting, setIncludeFronting] = useState(false);
  const [frontingShowCount, setFrontingShowCount] = useState(true);

  function reset() {
    setName("");
    setIncludeBio(false);
    setIncludeFronting(false);
    setFrontingShowCount(true);
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    const data: ShareViewCreate = {
      name: name.trim(),
      include_bio: includeBio,
      include_fronting: includeFronting,
      fronting_show_count: frontingShowCount,
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
          <CheckboxRow
            checked={includeBio}
            onChange={setIncludeBio}
            label="Include member bios"
            desc="Show each shown member's bio."
          />
          <CheckboxRow
            checked={includeFronting}
            onChange={setIncludeFronting}
            label="Show who's currently fronting"
            desc="Adds a live 'who's fronting now' view. Front history is never shown."
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
  const [confirmDelete, setConfirmDelete] = useState(false);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base flex items-center gap-2">
            {view.name}
            {view.is_shared && (
              <Badge className="text-[10px]">shared</Badge>
            )}
          </CardTitle>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-xs text-destructive hover:text-destructive"
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <ViewSettings view={view} safety={safety} />
        <ViewMembers view={view} safety={safety} />
        <ViewFields view={view} safety={safety} />
        <PublishSection view={view} grants={grants} safety={safety} systemId={systemId} />
      </CardContent>

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

function ViewSettings({ view, safety }: { view: ShareView; safety: SafetyContext }) {
  const update = useUpdateShareView();
  const [reauth, setReauth] = useState<null | { field: "include_bio" | "include_fronting" | "fronting_show_count"; value: boolean }>(null);

  // Turning an option ON while the view is shared is a loosening; when safety
  // is armed it needs re-auth. Turning off is always immediate.
  function change(field: "include_bio" | "include_fronting" | "fronting_show_count", value: boolean) {
    const loosening = value && view.is_shared;
    if (loosening && safety.safeguarded && safety.tier !== "none") {
      setReauth({ field, value });
      return;
    }
    update.mutate({ id: view.id, data: { [field]: value } });
  }

  return (
    <div className="space-y-2">
      <CheckboxRow
        checked={view.include_bio}
        onChange={(v) => change("include_bio", v)}
        label="Include member bios"
      />
      <CheckboxRow
        checked={view.include_fronting}
        onChange={(v) => change("include_fronting", v)}
        label="Show who's currently fronting"
      />
      {view.include_fronting && (
        <CheckboxRow
          checked={view.fronting_show_count}
          onChange={(v) => change("fronting_show_count", v)}
          label="Count fronting members not in this view"
          indent
        />
      )}
      {reauth && (
        <DestructiveConfirmDialog
          open
          onOpenChange={(o) => !o && setReauth(null)}
          title="Confirm change"
          description="This exposes more on an already-shared view, so it waits out the grace period and needs confirmation."
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
  const addMember = useAddViewMember();
  const removeMember = useRemoveViewMember();
  const addGroup = useAddViewGroup();
  const [pendingAdd, setPendingAdd] = useState<string | null>(null);

  const memberById = useMemo(() => {
    const m = new Map<string, { label: string; isPublic: boolean }>();
    for (const mem of members ?? [])
      m.set(mem.id, {
        label: mem.display_name || mem.name,
        isPublic: mem.privacy === "public",
      });
    return m;
  }, [members]);

  const inView = new Set(view.members.map((m) => m.member_id));
  const addable = (members ?? []).filter(
    (m) => !inView.has(m.id) && !m.never_shareable,
  );
  // Members that are in the view but won't actually appear, because their
  // privacy keeps them off the public tier. Surfaced so "why isn't X showing?"
  // never becomes a mystery.
  const hiddenInView = view.members.filter(
    (row) => memberById.get(row.member_id)?.isPublic === false,
  ).length;

  function doAdd(memberId: string, reauth?: DestructiveConfirm) {
    addMember.mutate({ viewId: view.id, memberId, reauth });
  }

  function onPick(memberId: string) {
    if (view.is_shared && safety.safeguarded && safety.tier !== "none") {
      setPendingAdd(memberId);
    } else {
      doAdd(memberId);
    }
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
            const info = memberById.get(row.member_id);
            const hidden = info?.isPublic === false;
            return (
              <Badge
                key={row.id}
                variant={hidden ? "outline" : "secondary"}
                className={`gap-1 ${hidden ? "text-muted-foreground" : ""}`}
                title={
                  hidden
                    ? "This member's privacy isn't Public, so they won't appear. Set their privacy to Public to show them."
                    : undefined
                }
              >
                {info?.label ?? "member"}
                {hidden && <span className="text-[9px]">won't show</span>}
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
          appear: only members whose privacy is Public show on a public profile
          or link. Change their privacy in the member editor to show them.
        </p>
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
              addable.map((m) => (
                <SelectItem key={m.id} value={m.id}>
                  {m.display_name || m.name}
                </SelectItem>
              ))
            )}
          </SelectContent>
        </Select>
        <Select
          value=""
          onValueChange={(gid) =>
            addGroup.mutate({
              viewId: view.id,
              groupId: gid,
              reauth: undefined,
            })
          }
        >
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
          description="This view is already published, so adding a member exposes them after the grace period. Confirm to continue."
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
    </div>
  );
}

function ViewFields({ view, safety }: { view: ShareView; safety: SafetyContext }) {
  const { data: fields } = useCustomFields();
  const addField = useAddViewField();
  const removeField = useRemoveViewField();
  const [pendingAdd, setPendingAdd] = useState<string | null>(null);

  const fieldById = useMemo(() => {
    const m = new Map<string, string>();
    for (const f of fields ?? []) m.set(f.id, f.name);
    return m;
  }, [fields]);

  const inView = new Set(view.fields.map((f) => f.field_id));
  const addable = (fields ?? []).filter((f) => !inView.has(f.id));

  function doAdd(fieldId: string, reauth?: DestructiveConfirm) {
    addField.mutate({ viewId: view.id, fieldId, reauth });
  }

  function onPick(fieldId: string) {
    if (view.is_shared && safety.safeguarded && safety.tier !== "none") {
      setPendingAdd(fieldId);
    } else {
      doAdd(fieldId);
    }
  }

  if ((fields ?? []).length === 0) return null;

  return (
    <div className="space-y-2">
      <Label className="text-xs uppercase tracking-wide text-muted-foreground">
        Custom fields shown
      </Label>
      {view.fields.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {view.fields.map((row) => (
            <Badge key={row.id} variant="secondary" className="gap-1">
              {fieldById.get(row.field_id) ?? "field"}
              <button
                type="button"
                className="ml-0.5 hover:text-destructive"
                onClick={() => removeField.mutate({ viewId: view.id, fieldId: row.field_id })}
                aria-label="Remove"
              >
                ×
              </button>
            </Badge>
          ))}
        </div>
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
          description="This view is already published, so exposing a field takes effect after the grace period. Confirm to continue."
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
  const { user } = useAuth();
  const attest = useAttestAdult();
  const createGrant = useCreateShareGrant();
  const rotate = useRotateShareGrant();
  const revoke = useRevokeShareGrant();

  const [publishing, setPublishing] = useState<null | "public" | "link">(null);
  const [tokenShown, setTokenShown] = useState<ShareGrantCreated | null>(null);

  const hasPublic = grants.some((g) => g.subject_type === "public");
  const origin = typeof window !== "undefined" ? window.location.origin : "";

  function beginPublish(kind: "public" | "link") {
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
                      onClick={() =>
                        rotate.mutate(g.id, {
                          onSuccess: (res) => setTokenShown(res),
                        })
                      }
                    >
                      Rotate
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs text-destructive hover:text-destructive"
                    disabled={revoke.isPending}
                    onClick={() => revoke.mutate(g.id)}
                  >
                    Unpublish
                  </Button>
                </div>
              </div>
              {g.subject_type === "public" && (
                <CopyableUrl url={grantUrl(g)} />
              )}
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
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-xs"
            onClick={() => beginPublish("public")}
          >
            <Globe className="mr-1 h-3.5 w-3.5" /> Make public
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          className="h-8 text-xs"
          onClick={() => beginPublish("link")}
        >
          <Link2 className="mr-1 h-3.5 w-3.5" /> Create share link
        </Button>
      </div>

      {publishing && (
        <PublishDialog
          kind={publishing}
          needsAttestation={!!user && user.adult_attested_at === null}
          safety={safety}
          onCancel={() => setPublishing(null)}
          busy={attest.isPending || createGrant.isPending}
          onConfirm={async (creds) => {
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
              },
            );
          }}
        />
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
  onConfirm,
  onCancel,
  busy,
}: {
  kind: "public" | "link";
  needsAttestation: boolean;
  safety: SafetyContext;
  onConfirm: (creds: { password?: string; totp_code?: string }) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const { user } = useAuth();
  const [attested, setAttested] = useState(false);
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");

  const needsPassword =
    safety.safeguarded && (safety.tier === "password" || safety.tier === "both");
  const needsTotp =
    safety.safeguarded &&
    (safety.tier === "totp" || safety.tier === "both") &&
    !!user?.totp_enabled;

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
            {safety.safeguarded && " It takes effect after your grace period."}
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
                inputMode="numeric"
                maxLength={6}
                autoComplete="off"
              />
            </div>
          )}
        </div>
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

function TokenDialog({ url, onClose }: { url: string; onClose: () => void }) {
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Your share link</DialogTitle>
          <DialogDescription>
            Copy it now - it is shown only once. Anyone with this link can see
            the view until you rotate or unpublish it.
          </DialogDescription>
        </DialogHeader>
        <CopyableUrl url={url} big />
        <DialogFooter>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CopyableUrl({ url, big }: { url: string; big?: boolean }) {
  return (
    <div className={`mt-1 flex items-center gap-1 ${big ? "" : "text-xs"}`}>
      <code className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1 text-xs">
        {url}
      </code>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 px-2"
        onClick={() => {
          navigator.clipboard.writeText(url);
          toast.success("Copied");
        }}
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
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  desc?: string;
  indent?: boolean;
}) {
  return (
    <label className={`flex items-start gap-3 cursor-pointer ${indent ? "ml-6" : ""}`}>
      <Checkbox
        checked={checked}
        onCheckedChange={(v) => onChange(v === true)}
        className="mt-0.5"
      />
      <div>
        <span className="text-sm font-medium">{label}</span>
        {desc && <p className="text-xs text-muted-foreground mt-0.5">{desc}</p>}
      </div>
    </label>
  );
}
