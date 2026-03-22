import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { getAdminUsers, updateAdminUser, type AdminUser, type AdminUserPatch } from "@/lib/admin";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

function UserRow({ user }: { user: AdminUser }) {
  const qc = useQueryClient();
  const [tier, setTier] = useState(user.tier);
  const [isAdmin, setIsAdmin] = useState(user.is_admin);
  const [memberLimit, setMemberLimit] = useState(
    user.member_limit != null ? String(user.member_limit) : "",
  );
  const [dirty, setDirty] = useState(false);

  const mutation = useMutation({
    mutationFn: (patch: AdminUserPatch) => updateAdminUser(user.id, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      setDirty(false);
    },
  });

  function handleSave() {
    const limit = memberLimit === "" ? null : Number(memberLimit);
    mutation.mutate({ tier, is_admin: isAdmin, member_limit: limit });
  }

  return (
    <tr className="border-b text-sm last:border-0">
      <td className="py-2 pr-4 font-mono text-xs text-muted-foreground">{user.email}</td>
      <td className="py-2 pr-4">
        <Select
          value={tier}
          onValueChange={(v) => { setTier(v); setDirty(true); }}
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
          onChange={(e) => { setMemberLimit(e.target.value); setDirty(true); }}
          type="number"
          min={0}
        />
      </td>
      <td className="py-2 pr-4">
        <Select
          value={isAdmin ? "yes" : "no"}
          onValueChange={(v) => { setIsAdmin(v === "yes"); setDirty(true); }}
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
      <td className="py-2 pr-4 text-xs text-muted-foreground">{user.member_count}</td>
      <td className="py-2 pr-4 text-xs text-muted-foreground">{formatBytes(user.storage_used_bytes)}</td>
      <td className="py-2">
        {dirty && (
          <Button size="sm" className="h-7 text-xs" onClick={handleSave} disabled={mutation.isPending}>
            Save
          </Button>
        )}
      </td>
    </tr>
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
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          className="max-w-xs"
        />
        <Card>
          <CardContent className="p-0">
            <table className="w-full">
              <thead>
                <tr className="border-b text-xs text-muted-foreground">
                  <th className="py-2 pr-4 text-left font-medium">Email</th>
                  <th className="py-2 pr-4 text-left font-medium">Tier</th>
                  <th className="py-2 pr-4 text-left font-medium">Member limit</th>
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
                    <td colSpan={7} className="py-6 text-center text-sm text-muted-foreground">
                      No users found
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </CardContent>
        </Card>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
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
