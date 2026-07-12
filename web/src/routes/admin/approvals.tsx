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
  bulkApprove,
  type PendingUser,
} from "@/lib/admin";
import { timeAgo } from "@/lib/utils";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { Check, X } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";

function ApprovalRow({
  user,
  selected,
  onToggleSelected,
}: {
  user: PendingUser;
  selected: boolean;
  onToggleSelected: (checked: boolean) => void;
}) {
  const qc = useQueryClient();
  const { formatDateTime } = useDateFormatters();
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
      <td className="py-3 pr-3 align-middle">
        <Checkbox
          checked={selected}
          onCheckedChange={(c) => onToggleSelected(c === true)}
          aria-label={`Select ${user.email}`}
        />
      </td>
      <td className="py-3 pr-4 font-mono text-xs">{user.email}</td>
      <td className="py-3 pr-4 text-xs text-muted-foreground">
        {user.signup_ip ?? "—"}
      </td>
      <td className="py-3 pr-4 text-xs text-muted-foreground">
        <span title={formatDateTime(user.created_at)}>
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
  const qc = useQueryClient();
  const { data: users, isLoading } = useQuery({
    queryKey: ["admin", "approvals"],
    queryFn: getPendingApprovals,
    refetchInterval: 30000,
  });

  const [selected, setSelected] = useState<Set<string>>(new Set());

  const visibleIds = (users ?? []).map((u) => u.id);
  const selectedVisibleCount = visibleIds.filter((id) => selected.has(id))
    .length;
  const allVisibleSelected =
    visibleIds.length > 0 && selectedVisibleCount === visibleIds.length;

  const bulk = useMutation({
    mutationFn: (ids: string[]) => bulkApprove(ids),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["admin", "approvals"] });
      qc.invalidateQueries({ queryKey: ["admin", "stats"] });
      const skipped = data.results.length - data.approved_count;
      if (skipped > 0) {
        toast.success(
          `Approved ${data.approved_count} (${skipped} skipped — see console)`,
        );
      } else {
        toast.success(`Approved ${data.approved_count}`);
      }
      setSelected(new Set());
    },
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
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                {count} account{count !== 1 ? "s" : ""} waiting for approval
                {selectedVisibleCount > 0 ? ` • ${selectedVisibleCount} selected` : ""}
              </p>
              <Button
                size="sm"
                variant="default"
                disabled={selectedVisibleCount === 0 || bulk.isPending}
                onClick={() => bulk.mutate(Array.from(selected))}
              >
                <Check className="h-3 w-3 mr-1" />
                Approve selected ({selectedVisibleCount})
              </Button>
            </div>
            <Card>
              <CardContent className="p-0">
                <table className="w-full">
                  <thead>
                    <tr className="border-b text-xs text-muted-foreground">
                      <th className="py-2 pr-3 text-left font-medium w-8">
                        <Checkbox
                          checked={allVisibleSelected}
                          onCheckedChange={(c) => {
                            setSelected((prev) => {
                              const next = new Set(prev);
                              if (c === true) {
                                visibleIds.forEach((id) => next.add(id));
                              } else {
                                visibleIds.forEach((id) => next.delete(id));
                              }
                              return next;
                            });
                          }}
                          aria-label="Select all visible"
                        />
                      </th>
                      <th className="py-2 pr-4 text-left font-medium">Email</th>
                      <th className="py-2 pr-4 text-left font-medium">IP</th>
                      <th className="py-2 pr-4 text-left font-medium">Signed up</th>
                      <th className="py-2 pr-4 text-left font-medium">Email</th>
                      <th className="py-2 text-right font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {users?.map((u) => (
                      <ApprovalRow
                        key={u.id}
                        user={u}
                        selected={selected.has(u.id)}
                        onToggleSelected={(checked) => {
                          setSelected((prev) => {
                            const next = new Set(prev);
                            if (checked) next.add(u.id);
                            else next.delete(u.id);
                            return next;
                          });
                        }}
                      />
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
