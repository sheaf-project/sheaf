import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  getInvites,
  createInvite,
  deleteInvite,
  type InviteCode,
} from "@/lib/admin";
import { timeAgo } from "@/lib/utils";
import { Copy, Plus, Trash2 } from "lucide-react";

function InviteRow({ invite }: { invite: InviteCode }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [copied, setCopied] = useState(false);

  const remove = useMutation({
    mutationFn: () => deleteInvite(invite.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "invites"] });
      toast.success("Invite deleted");
    },
  });

  function copyCode() {
    navigator.clipboard.writeText(invite.code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const usesLabel =
    invite.max_uses === 0
      ? `${invite.use_count} / \u221e`
      : `${invite.use_count} / ${invite.max_uses}`;

  const isExpired =
    invite.expires_at && new Date(invite.expires_at) < new Date();
  const isMaxed =
    invite.max_uses > 0 && invite.use_count >= invite.max_uses;

  return (
    <tr className="border-b text-sm last:border-0">
      <td className="py-3 pr-4">
        <div className="flex items-center gap-2">
          <code className="text-xs">{invite.code}</code>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 w-6 p-0"
            onClick={copyCode}
            title="Copy code"
          >
            <Copy className="h-3 w-3" />
          </Button>
          {copied && (
            <span className="text-xs text-muted-foreground">Copied</span>
          )}
        </div>
      </td>
      <td className="py-3 pr-4 text-xs text-muted-foreground">{usesLabel}</td>
      <td className="py-3 pr-4 text-xs text-muted-foreground">
        {invite.note ?? "—"}
      </td>
      <td className="py-3 pr-4 text-xs text-muted-foreground">
        {invite.expires_at ? (
          <span className={isExpired ? "text-destructive" : ""}>
            {isExpired ? "Expired" : new Date(invite.expires_at).toLocaleDateString()}
          </span>
        ) : (
          "Never"
        )}
      </td>
      <td className="py-3 pr-4 text-xs text-muted-foreground">
        <span title={new Date(invite.created_at).toLocaleString()}>
          {timeAgo(invite.created_at)}
        </span>
        {invite.created_by_email && (
          <span className="ml-1">by {invite.created_by_email}</span>
        )}
      </td>
      <td className="py-3 text-right">
        {confirming ? (
          <div className="flex items-center justify-end gap-2">
            <span className="text-xs text-muted-foreground">Delete?</span>
            <Button
              size="sm"
              variant="destructive"
              className="h-7 text-xs"
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
            >
              Confirm
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => setConfirming(false)}
              disabled={remove.isPending}
            >
              Cancel
            </Button>
          </div>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs text-destructive hover:text-destructive"
            onClick={() => setConfirming(true)}
            disabled={isExpired || isMaxed ? false : false}
          >
            <Trash2 className="h-3 w-3 mr-1" />
            Delete
          </Button>
        )}
      </td>
    </tr>
  );
}

function CreateInviteForm({ onCreated }: { onCreated: () => void }) {
  const [maxUses, setMaxUses] = useState("");
  const [note, setNote] = useState("");
  const [expiresIn, setExpiresIn] = useState("");

  const create = useMutation({
    mutationFn: () => {
      const body: { max_uses?: number; note?: string; expires_at?: string } =
        {};
      if (maxUses) body.max_uses = parseInt(maxUses, 10);
      if (note) body.note = note;
      if (expiresIn) {
        const days = parseInt(expiresIn, 10);
        if (days > 0) {
          const dt = new Date();
          dt.setDate(dt.getDate() + days);
          body.expires_at = dt.toISOString();
        }
      }
      return createInvite(body);
    },
    onSuccess: () => {
      setMaxUses("");
      setNote("");
      setExpiresIn("");
      onCreated();
      toast.success("Invite created");
    },
  });

  return (
    <Card>
      <CardContent className="pt-6">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
          className="flex flex-wrap items-end gap-4"
        >
          <div className="space-y-1">
            <Label htmlFor="invite-max-uses" className="text-xs">
              Max uses
            </Label>
            <Input
              id="invite-max-uses"
              type="number"
              min="0"
              placeholder="0 = unlimited"
              value={maxUses}
              onChange={(e) => setMaxUses(e.target.value)}
              className="w-32"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="invite-expires" className="text-xs">
              Expires in (days)
            </Label>
            <Input
              id="invite-expires"
              type="number"
              min="1"
              placeholder="Never"
              value={expiresIn}
              onChange={(e) => setExpiresIn(e.target.value)}
              className="w-32"
            />
          </div>
          <div className="space-y-1 flex-1 min-w-48">
            <Label htmlFor="invite-note" className="text-xs">
              Note
            </Label>
            <Input
              id="invite-note"
              placeholder="Optional note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
          <Button type="submit" disabled={create.isPending} className="h-9">
            <Plus className="h-4 w-4 mr-1" />
            {create.isPending ? "Creating..." : "Create invite"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

export function AdminInvitesPage() {
  const qc = useQueryClient();
  const { data: invites, isLoading } = useQuery({
    queryKey: ["admin", "invites"],
    queryFn: getInvites,
  });

  const count = invites?.length ?? 0;

  return (
    <>
      <PageHeader title="Invite Codes" />
      <div className="max-w-5xl space-y-4">
        <CreateInviteForm
          onCreated={() =>
            qc.invalidateQueries({ queryKey: ["admin", "invites"] })
          }
        />

        {isLoading ? null : count === 0 ? (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No invite codes yet
            </CardContent>
          </Card>
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              {count} invite code{count !== 1 ? "s" : ""}
            </p>
            <Card>
              <CardContent className="p-0">
                <table className="w-full">
                  <thead>
                    <tr className="border-b text-xs text-muted-foreground">
                      <th className="py-2 pr-4 text-left font-medium">Code</th>
                      <th className="py-2 pr-4 text-left font-medium">Uses</th>
                      <th className="py-2 pr-4 text-left font-medium">Note</th>
                      <th className="py-2 pr-4 text-left font-medium">
                        Expires
                      </th>
                      <th className="py-2 pr-4 text-left font-medium">
                        Created
                      </th>
                      <th className="py-2 text-right font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {invites?.map((inv) => (
                      <InviteRow key={inv.id} invite={inv} />
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </>
  );
}
