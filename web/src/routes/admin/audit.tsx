import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getAdminAuditEvents, type AdminAuditEvent } from "@/lib/admin";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const ACTION_OPTIONS = [
  "",
  "user_update",
  "user_approve",
  "user_reject",
  "user_member_limit_set",
  "user_safety_reset",
  "user_pending_bypass",
  "user_session_revoke",
  "user_api_keys_rotate_all",
  "user_suspend",
  "user_unsuspend",
  "user_dossier_export",
  "import_log_view",
];

function formatDiff(
  before: Record<string, unknown> | null,
  after: Record<string, unknown> | null,
): string {
  if (!before && !after) return "";
  const keys = new Set([
    ...Object.keys(before ?? {}),
    ...Object.keys(after ?? {}),
  ]);
  const parts: string[] = [];
  for (const k of keys) {
    const b = before?.[k];
    const a = after?.[k];
    parts.push(`${k}: ${JSON.stringify(b)} -> ${JSON.stringify(a)}`);
  }
  return parts.join(", ");
}

export function AdminAuditPage() {
  const [targetUserId, setTargetUserId] = useState("");
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);

  const filters = useMemo(
    () => ({
      target_user_id: targetUserId || undefined,
      action: action || undefined,
      page,
      limit: 50,
    }),
    [targetUserId, action, page],
  );

  const { data: events } = useQuery({
    queryKey: ["admin", "audit", filters],
    queryFn: () => getAdminAuditEvents(filters),
  });

  return (
    <>
      <PageHeader title="Admin audit log" />
      <p className="text-sm text-muted-foreground max-w-prose mb-4">
        Append-only log of state-changing admin actions. Browsing user data
        (list, detail, search) is deliberately not logged so the table stays
        signal-rich for abuse detection.
      </p>
      <div className="grid gap-3 sm:grid-cols-3 mb-4">
        <div className="space-y-1">
          <Label htmlFor="audit-target" className="text-xs">
            Target user ID
          </Label>
          <Input
            id="audit-target"
            value={targetUserId}
            onChange={(e) => {
              setTargetUserId(e.target.value);
              setPage(1);
            }}
            placeholder="UUID"
            className="font-mono text-xs"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="audit-action" className="text-xs">
            Action
          </Label>
          <select
            id="audit-action"
            value={action}
            onChange={(e) => {
              setAction(e.target.value);
              setPage(1);
            }}
            className="h-10 w-full rounded-md border bg-background px-3 text-sm"
          >
            {ACTION_OPTIONS.map((a) => (
              <option key={a} value={a}>
                {a || "(all)"}
              </option>
            ))}
          </select>
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          <table className="w-full">
            <thead>
              <tr className="border-b text-xs text-muted-foreground">
                <th className="py-2 px-3 text-left font-medium">When</th>
                <th className="py-2 px-3 text-left font-medium">Admin</th>
                <th className="py-2 px-3 text-left font-medium">Action</th>
                <th className="py-2 px-3 text-left font-medium">Target</th>
                <th className="py-2 px-3 text-left font-medium">Diff / reason</th>
              </tr>
            </thead>
            <tbody>
              {events?.map((e: AdminAuditEvent) => (
                <tr key={e.id} className="border-b text-sm last:border-0">
                  <td className="py-2 px-3 align-top whitespace-nowrap">
                    {new Date(e.created_at).toLocaleString()}
                  </td>
                  <td className="py-2 px-3 align-top">
                    {e.admin_email ?? <span className="text-muted-foreground">deleted</span>}
                  </td>
                  <td className="py-2 px-3 align-top">
                    <Badge variant="outline" className="text-[10px]">
                      {e.action}
                    </Badge>
                  </td>
                  <td className="py-2 px-3 align-top font-mono text-xs text-muted-foreground">
                    <div>{e.target_type}</div>
                    <div>{e.target_user_id ?? e.target_id ?? ""}</div>
                  </td>
                  <td className="py-2 px-3 align-top text-xs">
                    {e.reason && (
                      <div className="italic mb-1">"{e.reason}"</div>
                    )}
                    <div className="font-mono text-muted-foreground">
                      {formatDiff(e.before_json, e.after_json)}
                    </div>
                  </td>
                </tr>
              ))}
              {events?.length === 0 && (
                <tr>
                  <td
                    colSpan={5}
                    className="py-6 px-3 text-center text-sm text-muted-foreground"
                  >
                    No audit events match the current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <div className="mt-4 flex items-center justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={page === 1}
          onClick={() => setPage((p) => Math.max(1, p - 1))}
        >
          Previous
        </Button>
        <span className="text-xs text-muted-foreground">Page {page}</span>
        <Button
          variant="outline"
          size="sm"
          disabled={!events || events.length < 50}
          onClick={() => setPage((p) => p + 1)}
        >
          Next
        </Button>
      </div>
    </>
  );
}
