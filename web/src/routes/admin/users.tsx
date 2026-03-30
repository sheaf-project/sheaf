import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  getAdminUsers,
  updateAdminUser,
  resetUserPassword,
  changeUserEmail,
  disableUserTotp,
  verifyUserEmail,
  type AdminUser,
  type AdminUserPatch,
} from "@/lib/admin";
import { ChevronDown, ChevronRight, Copy } from "lucide-react";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

function UserActions({ user }: { user: AdminUser }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState<string | null>(null);
  const [newEmail, setNewEmail] = useState("");
  const [generatedPassword, setGeneratedPassword] = useState<string | null>(null);

  const resetPw = useMutation({
    mutationFn: () => resetUserPassword(user.id),
    onSuccess: (data) => {
      setGeneratedPassword(data.password);
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.success(
        `Password reset — ${data.sessions_revoked} session(s) revoked`,
      );
      setConfirming(null);
    },
  });

  const emailChange = useMutation({
    mutationFn: () => changeUserEmail(user.id, newEmail),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.success("Email changed");
      setNewEmail("");
      setConfirming(null);
    },
  });

  const disableTotp = useMutation({
    mutationFn: () => disableUserTotp(user.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.success("TOTP disabled");
      setConfirming(null);
    },
  });

  const verifyEmail = useMutation({
    mutationFn: () => verifyUserEmail(user.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.success("Email verified");
      setConfirming(null);
    },
  });

  const isPending =
    resetPw.isPending ||
    emailChange.isPending ||
    disableTotp.isPending ||
    verifyEmail.isPending;

  function copyPassword() {
    if (generatedPassword) {
      navigator.clipboard.writeText(generatedPassword);
      toast.success("Password copied");
    }
  }

  return (
    <div className="space-y-3 py-2">
      {/* Generated password display */}
      {generatedPassword && (
        <div className="flex items-center gap-2 rounded bg-muted p-2">
          <span className="text-xs text-muted-foreground">New password:</span>
          <code className="text-xs font-mono">{generatedPassword}</code>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 w-6 p-0"
            onClick={copyPassword}
          >
            <Copy className="h-3 w-3" />
          </Button>
          <span className="text-xs text-muted-foreground ml-auto">
            Shown once — copy it now
          </span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {/* Reset password */}
        {confirming === "reset-password" ? (
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">
              Generate random password and revoke sessions?
            </span>
            <Button
              size="sm"
              variant="destructive"
              className="h-7 text-xs"
              onClick={() => resetPw.mutate()}
              disabled={isPending}
            >
              Confirm
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => setConfirming(null)}
              disabled={isPending}
            >
              Cancel
            </Button>
          </div>
        ) : (
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            onClick={() => setConfirming("reset-password")}
          >
            Reset password
          </Button>
        )}

        {/* Change email */}
        {confirming === "change-email" ? (
          <div className="flex items-center gap-2">
            <Input
              className="h-7 w-52 text-xs"
              placeholder="new@email.com"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
            />
            <Button
              size="sm"
              variant="destructive"
              className="h-7 text-xs"
              onClick={() => emailChange.mutate()}
              disabled={isPending || !newEmail}
            >
              Confirm
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => {
                setConfirming(null);
                setNewEmail("");
              }}
              disabled={isPending}
            >
              Cancel
            </Button>
          </div>
        ) : (
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            onClick={() => setConfirming("change-email")}
          >
            Change email
          </Button>
        )}

        {/* Disable TOTP */}
        {user.totp_enabled && (
          confirming === "disable-totp" ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">
                Disable 2FA?
              </span>
              <Button
                size="sm"
                variant="destructive"
                className="h-7 text-xs"
                onClick={() => disableTotp.mutate()}
                disabled={isPending}
              >
                Confirm
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => setConfirming(null)}
                disabled={isPending}
              >
                Cancel
              </Button>
            </div>
          ) : (
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => setConfirming("disable-totp")}
            >
              Disable 2FA
            </Button>
          )
        )}

        {/* Verify email */}
        {!user.email_verified && (
          confirming === "verify-email" ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">
                Mark email verified?
              </span>
              <Button
                size="sm"
                className="h-7 text-xs"
                onClick={() => verifyEmail.mutate()}
                disabled={isPending}
              >
                Confirm
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => setConfirming(null)}
                disabled={isPending}
              >
                Cancel
              </Button>
            </div>
          ) : (
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => setConfirming("verify-email")}
            >
              Verify email
            </Button>
          )
        )}
      </div>
    </div>
  );
}

function UserRow({ user }: { user: AdminUser }) {
  const qc = useQueryClient();
  const [tier, setTier] = useState(user.tier);
  const [isAdmin, setIsAdmin] = useState(user.is_admin);
  const [memberLimit, setMemberLimit] = useState(
    user.member_limit != null ? String(user.member_limit) : "",
  );
  const [dirty, setDirty] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const mutation = useMutation({
    mutationFn: (patch: AdminUserPatch) => updateAdminUser(user.id, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      setDirty(false);
      toast.success("User updated");
    },
  });

  function handleSave() {
    const limit = memberLimit === "" ? null : Number(memberLimit);
    mutation.mutate({ tier, is_admin: isAdmin, member_limit: limit });
  }

  return (
    <>
      <tr className="border-b text-sm last:border-0">
        <td className="py-2 pr-4">
          <button
            className="flex items-center gap-1 font-mono text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? (
              <ChevronDown className="h-3 w-3 shrink-0" />
            ) : (
              <ChevronRight className="h-3 w-3 shrink-0" />
            )}
            {user.email}
          </button>
        </td>
        <td className="py-2 pr-4">
          <div className="flex items-center gap-1">
            {user.totp_enabled && (
              <Badge variant="secondary" className="text-[10px] px-1 py-0">
                2FA
              </Badge>
            )}
            {!user.email_verified && (
              <Badge
                variant="outline"
                className="text-[10px] px-1 py-0 text-muted-foreground"
              >
                Unverified
              </Badge>
            )}
          </div>
        </td>
        <td className="py-2 pr-4">
          <Select
            value={tier}
            onValueChange={(v) => {
              setTier(v);
              setDirty(true);
            }}
          >
            <SelectTrigger className="h-7 w-28 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="free">Free</SelectItem>
              <SelectItem value="plus">Plus</SelectItem>
              <SelectItem value="self_hosted">Self-hosted</SelectItem>
            </SelectContent>
          </Select>
        </td>
        <td className="py-2 pr-4">
          <Input
            className="h-7 w-20 text-xs"
            placeholder="∞"
            value={memberLimit}
            onChange={(e) => {
              setMemberLimit(e.target.value);
              setDirty(true);
            }}
            type="number"
            min={0}
          />
        </td>
        <td className="py-2 pr-4">
          <Select
            value={isAdmin ? "yes" : "no"}
            onValueChange={(v) => {
              setIsAdmin(v === "yes");
              setDirty(true);
            }}
          >
            <SelectTrigger className="h-7 w-20 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="no">No</SelectItem>
              <SelectItem value="yes">Yes</SelectItem>
            </SelectContent>
          </Select>
        </td>
        <td className="py-2 pr-4 text-xs text-muted-foreground">
          {user.member_count}
        </td>
        <td className="py-2 pr-4 text-xs text-muted-foreground">
          {formatBytes(user.storage_used_bytes)}
        </td>
        <td className="py-2">
          {dirty && (
            <Button
              size="sm"
              className="h-7 text-xs"
              onClick={handleSave}
              disabled={mutation.isPending}
            >
              Save
            </Button>
          )}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b last:border-0">
          <td colSpan={8} className="px-6 pb-3">
            <UserActions user={user} />
          </td>
        </tr>
      )}
    </>
  );
}

export function AdminUsersPage() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const { data: users } = useQuery({
    queryKey: ["admin", "users", search, page],
    queryFn: () => getAdminUsers(search || undefined, page),
  });

  return (
    <>
      <PageHeader title="Users" />
      <div className="max-w-5xl space-y-4">
        <Input
          placeholder="Search by email..."
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
          className="max-w-xs"
        />
        <Card>
          <CardContent className="p-0">
            <table className="w-full">
              <thead>
                <tr className="border-b text-xs text-muted-foreground">
                  <th className="py-2 pr-4 text-left font-medium">Email</th>
                  <th className="py-2 pr-4 text-left font-medium">Status</th>
                  <th className="py-2 pr-4 text-left font-medium">Tier</th>
                  <th className="py-2 pr-4 text-left font-medium">
                    Member limit
                  </th>
                  <th className="py-2 pr-4 text-left font-medium">Admin</th>
                  <th className="py-2 pr-4 text-left font-medium">Members</th>
                  <th className="py-2 pr-4 text-left font-medium">Storage</th>
                  <th className="py-2 text-left font-medium" />
                </tr>
              </thead>
              <tbody>
                {users?.map((u) => <UserRow key={u.id} user={u} />)}
                {users?.length === 0 && (
                  <tr>
                    <td
                      colSpan={8}
                      className="py-6 text-center text-sm text-muted-foreground"
                    >
                      No users found
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </CardContent>
        </Card>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!users || users.length < 50}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </div>
      </div>
    </>
  );
}
