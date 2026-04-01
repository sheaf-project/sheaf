import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  getPendingApprovals,
  approveUser,
  rejectUser,
  type PendingUser,
} from "@/lib/admin";
import { timeAgo } from "@/lib/utils";
import { Check, X } from "lucide-react";

function ApprovalRow({ user }: { user: PendingUser }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState<"approve" | "reject" | null>(null);

  const approve = useMutation({
    mutationFn: () => approveUser(user.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "approvals"] });
      qc.invalidateQueries({ queryKey: ["admin", "stats"] });
      toast.success("User approved");
    },
  });

  const reject = useMutation({
    mutationFn: () => rejectUser(user.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "approvals"] });
      qc.invalidateQueries({ queryKey: ["admin", "stats"] });
      toast.success("User rejected");
    },
  });

  const isPending = approve.isPending || reject.isPending;

  return (
    <tr className="border-b text-sm last:border-0">
      <td className="py-3 pr-4 font-mono text-xs">{user.email}</td>
      <td className="py-3 pr-4 text-xs text-muted-foreground">
        {user.signup_ip ?? "—"}
      </td>
      <td className="py-3 pr-4 text-xs text-muted-foreground">
        <span title={new Date(user.created_at).toLocaleString()}>
          {timeAgo(user.created_at)}
        </span>
      </td>
      <td className="py-3 pr-4">
        {user.email_verified ? (
          <Badge variant="secondary" className="text-xs">Verified</Badge>
        ) : (
          <Badge variant="outline" className="text-xs text-muted-foreground">Unverified</Badge>
        )}
      </td>
      <td className="py-3 text-right">
        {confirming === "reject" ? (
          <div className="flex items-center justify-end gap-2">
            <span className="text-xs text-muted-foreground">Delete account?</span>
            <Button
              size="sm"
              variant="destructive"
              className="h-7 text-xs"
              onClick={() => reject.mutate()}
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
          <div className="flex items-center justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => {
                if (confirming === "approve") {
                  approve.mutate();
                } else {
                  setConfirming("approve");
                }
              }}
              disabled={isPending}
            >
              <Check className="h-3 w-3 mr-1" />
              {confirming === "approve" ? "Confirm" : "Approve"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs text-destructive hover:text-destructive"
              onClick={() => setConfirming("reject")}
              disabled={isPending}
            >
              <X className="h-3 w-3 mr-1" />
              Reject
            </Button>
          </div>
        )}
      </td>
    </tr>
  );
}

export function AdminApprovalsPage() {
  const { data: users, isLoading } = useQuery({
    queryKey: ["admin", "approvals"],
    queryFn: getPendingApprovals,
    refetchInterval: 30000,
  });

  const count = users?.length ?? 0;

  return (
    <>
      <PageHeader title="Approvals" />
      <div className="max-w-4xl space-y-4">
        {isLoading ? null : count === 0 ? (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No pending approvals
            </CardContent>
          </Card>
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              {count} account{count !== 1 ? "s" : ""} waiting for approval
            </p>
            <Card>
              <CardContent className="p-0">
                <table className="w-full">
                  <thead>
                    <tr className="border-b text-xs text-muted-foreground">
                      <th className="py-2 pr-4 text-left font-medium">Email</th>
                      <th className="py-2 pr-4 text-left font-medium">IP</th>
                      <th className="py-2 pr-4 text-left font-medium">Signed up</th>
                      <th className="py-2 pr-4 text-left font-medium">Email</th>
                      <th className="py-2 text-right font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {users?.map((u) => <ApprovalRow key={u.id} user={u} />)}
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
