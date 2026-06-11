import { useEffect, useState } from "react";
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
  adminBypassPendingActions,
  adminResetSystemSafety,
  banUser,
  downloadDossier,
  explainAccount,
  forceRotateApiKeys,
  getAdminUsers,
  listUserSessionsAdmin,
  listUserImportJobs,
  viewImportJobDetail,
  suspendUser,
  terminateUserSession,
  unbanUser,
  unsuspendUser,
  updateAdminUser,
  resetUserPassword,
  changeUserEmail,
  disableUserTotp,
  verifyUserEmail,
  type AdminUser,
  type AdminUserPatch,
  type AdminUserSession,
  type AdminImportJobSummary,
  type ExplainAccountResponse,
} from "@/lib/admin";
import { ChevronDown, ChevronRight, Copy } from "lucide-react";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

function SessionsSection({ userId }: { userId: string }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);

  const { data: sessions } = useQuery<AdminUserSession[]>({
    queryKey: ["admin", "sessions", userId],
    queryFn: () => listUserSessionsAdmin(userId),
  });

  const terminate = useMutation({
    mutationFn: ({ sid, why }: { sid: string; why: string }) =>
      terminateUserSession(userId, sid, why),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "sessions", userId] });
      qc.invalidateQueries({ queryKey: ["admin", "explain", userId] });
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      toast.success("Session revoked");
      setConfirming(null);
      setReason("");
    },
  });

  if (!sessions || sessions.length === 0) return null;

  return (
    <div>
      <div className="mb-1 text-muted-foreground">Sessions:</div>
      <ul className="space-y-1 font-mono">
        {sessions.map((s) => (
          <li key={s.id} className="flex items-start gap-2">
            <span className="flex-1 truncate">
              {s.nickname ? `${s.nickname} · ` : ""}
              {s.user_agent ?? "(unknown UA)"} · {s.ip ?? "—"}
              {s.created_at
                ? ` · ${new Date(s.created_at).toLocaleString()}`
                : ""}
            </span>
            {confirming === s.id ? (
              <div className="flex items-center gap-1">
                <Input
                  className="h-6 w-44 text-[10px]"
                  placeholder="Reason"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
                <Button
                  size="sm"
                  variant="destructive"
                  className="h-6 text-[10px]"
                  disabled={!reason.trim() || terminate.isPending}
                  onClick={() => terminate.mutate({ sid: s.id, why: reason })}
                >
                  Kill
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 text-[10px]"
                  onClick={() => {
                    setConfirming(null);
                    setReason("");
                  }}
                >
                  X
                </Button>
              </div>
            ) : (
              <Button
                size="sm"
                variant="outline"
                className="h-6 text-[10px]"
                onClick={() => setConfirming(s.id)}
              >
                Terminate
              </Button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ImportLogsSection({ userId }: { userId: string }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [openJobId, setOpenJobId] = useState<string | null>(null);

  const { data: jobs } = useQuery<AdminImportJobSummary[]>({
    queryKey: ["admin", "import-jobs", userId],
    queryFn: () => listUserImportJobs(userId),
  });

  // Viewing a job's events writes an `import_log_view` audit row server-side,
  // so the reason is required and the audit list is refreshed on success.
  const view = useMutation({
    mutationFn: ({ jobId, why }: { jobId: string; why: string }) =>
      viewImportJobDetail(jobId, why),
    onSuccess: (detail) => {
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      qc.invalidateQueries({ queryKey: ["admin", "explain", userId] });
      setOpenJobId(detail.id);
      setConfirming(null);
      setReason("");
    },
  });

  if (!jobs || jobs.length === 0) return null;

  return (
    <div>
      <div className="mb-1 text-muted-foreground">Import jobs:</div>
      <ul className="space-y-1 font-mono">
        {jobs.map((j) => {
          const detail =
            view.data && view.data.id === j.id && openJobId === j.id
              ? view.data
              : null;
          return (
            <li key={j.id} className="space-y-1">
              <div className="flex items-start gap-2">
                <span className="flex-1 truncate">
                  {j.source} · {j.status}
                  {j.created_at
                    ? ` · ${new Date(j.created_at).toLocaleString()}`
                    : ""}
                  {j.last_error ? " · error" : ""}
                </span>
                {confirming === j.id ? (
                  <div className="flex items-center gap-1">
                    <Input
                      className="h-6 w-44 text-[10px]"
                      placeholder="Reason"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                    <Button
                      size="sm"
                      variant="default"
                      className="h-6 text-[10px]"
                      disabled={!reason.trim() || view.isPending}
                      onClick={() => view.mutate({ jobId: j.id, why: reason })}
                    >
                      View
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-6 text-[10px]"
                      onClick={() => {
                        setConfirming(null);
                        setReason("");
                      }}
                    >
                      X
                    </Button>
                  </div>
                ) : detail ? (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 text-[10px]"
                    onClick={() => setOpenJobId(null)}
                  >
                    Hide
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 text-[10px]"
                    onClick={() => setConfirming(j.id)}
                  >
                    View logs
                  </Button>
                )}
              </div>
              {detail && (
                <div className="rounded border bg-background/50 p-1.5 text-[10px]">
                  {detail.last_error && (
                    <div className="mb-1 text-destructive">
                      error: {detail.last_error}
                    </div>
                  )}
                  {detail.events.length === 0 ? (
                    <div className="text-muted-foreground">(no events)</div>
                  ) : (
                    detail.events.map((ev, i) => (
                      <div
                        key={i}
                        className="flex gap-2 border-b border-border/50 py-0.5 last:border-0"
                      >
                        <span
                          className={`w-12 shrink-0 font-medium ${
                            ev.level === "error"
                              ? "text-destructive"
                              : ev.level === "warning"
                                ? "text-amber-600 dark:text-amber-500"
                                : "text-muted-foreground"
                          }`}
                        >
                          {ev.level}
                        </span>
                        <span className="w-20 shrink-0 text-muted-foreground">
                          {ev.stage}
                        </span>
                        <span className="flex-1 break-words">
                          {ev.message}
                          {ev.record_ref ? (
                            <span className="ml-1 text-muted-foreground">
                              ({ev.record_ref})
                            </span>
                          ) : null}
                        </span>
                      </div>
                    ))
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ExplainPanel({ userId }: { userId: string }) {
  const [open, setOpen] = useState(false);
  const { data, isLoading } = useQuery<ExplainAccountResponse>({
    queryKey: ["admin", "explain", userId],
    queryFn: () => explainAccount(userId),
    enabled: open,
    staleTime: 30_000,
  });

  return (
    <div className="rounded border bg-muted/30">
      <button
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-xs font-medium text-muted-foreground hover:text-foreground"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="flex items-center gap-1">
          {open ? (
            <ChevronDown className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )}
          Explain account
        </span>
        {open && data && (
          <span className="text-muted-foreground">
            {data.active_session_count} session(s) · {data.api_key_count} API key(s)
          </span>
        )}
      </button>
      {open && (
        <div className="space-y-2 border-t px-3 py-2 text-xs">
          {isLoading && (
            <p className="text-muted-foreground">Loading...</p>
          )}
          {data && (
            <>
              <div className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono">
                <div>
                  <span className="text-muted-foreground">Tier:</span> {data.tier}
                </div>
                <div>
                  <span className="text-muted-foreground">Status:</span>{" "}
                  {data.account_status}
                </div>
                <div>
                  <span className="text-muted-foreground">Email verified:</span>{" "}
                  {String(data.email_verified)}
                </div>
                <div>
                  <span className="text-muted-foreground">TOTP:</span>{" "}
                  {String(data.totp_enabled)}
                </div>
                <div>
                  <span className="text-muted-foreground">Signup IP:</span>{" "}
                  {data.signup_ip ?? "—"}
                </div>
                <div>
                  <span className="text-muted-foreground">Last login:</span>{" "}
                  {data.last_login_at
                    ? new Date(data.last_login_at).toLocaleString()
                    : "never"}
                </div>
                <div>
                  <span className="text-muted-foreground">Sessions:</span>{" "}
                  {data.active_session_count}
                </div>
                <div>
                  <span className="text-muted-foreground">API keys:</span>{" "}
                  {data.api_key_count}
                </div>
              </div>
              {data.system && (
                <div className="font-mono">
                  <span className="text-muted-foreground">System:</span>{" "}
                  {data.system.name || "(unnamed)"} ·{" "}
                  {data.system.member_count} members · auth=
                  {data.system.delete_confirmation} · grace=
                  {data.system.grace_period_days}d
                </div>
              )}
              <SessionsSection userId={userId} />
              <ImportLogsSection userId={userId} />
              {data.recent_admin_audit.length > 0 && (
                <div>
                  <div className="mb-1 text-muted-foreground">
                    Recent admin activity:
                  </div>
                  <ul className="space-y-0.5 font-mono">
                    {data.recent_admin_audit.slice(0, 5).map((row) => (
                      <li key={row.id}>
                        {new Date(row.created_at).toLocaleString()} ·{" "}
                        {row.action}
                        {row.reason ? ` — "${row.reason}"` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function UserActions({ user }: { user: AdminUser }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState<string | null>(null);
  const [newEmail, setNewEmail] = useState("");
  const [reason, setReason] = useState("");
  const [suspendDays, setSuspendDays] = useState("7");
  const [generatedPassword, setGeneratedPassword] = useState<string | null>(null);

  const resetPw = useMutation({
    mutationFn: () => resetUserPassword(user.id, reason),
    onSuccess: (data) => {
      setGeneratedPassword(data.password);
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      toast.success(
        `Password reset — ${data.sessions_revoked} session(s) revoked`,
      );
      setConfirming(null);
      setReason("");
    },
  });

  const resetSafety = useMutation({
    mutationFn: () => adminResetSystemSafety(user.id, reason),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      toast.success(
        data.changed_fields.length > 0
          ? `Safety reset (${data.changed_fields.length} fields cleared)`
          : "Safety already at default — no changes",
      );
      setConfirming(null);
      setReason("");
    },
  });

  const bypassPending = useMutation({
    mutationFn: () => adminBypassPendingActions(user.id, reason),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      toast.success(
        data.finalized_count > 0
          ? `Drained ${data.finalized_count} pending action(s)`
          : "No pending actions queued",
      );
      setConfirming(null);
      setReason("");
    },
  });

  const rotateKeys = useMutation({
    mutationFn: () => forceRotateApiKeys(user.id, reason),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      qc.invalidateQueries({ queryKey: ["admin", "explain", user.id] });
      toast.success(
        data.revoked_count > 0
          ? `Revoked ${data.revoked_count} API key(s)`
          : "No API keys to revoke",
      );
      setConfirming(null);
      setReason("");
    },
  });

  const suspend = useMutation({
    mutationFn: () => {
      const days = suspendDays.trim() === "" ? null : Number(suspendDays);
      return suspendUser(user.id, reason, days);
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      qc.invalidateQueries({ queryKey: ["admin", "explain", user.id] });
      toast.success(
        data.suspended_until
          ? `Suspended until ${new Date(data.suspended_until).toLocaleString()}`
          : "Suspended indefinitely",
      );
      setConfirming(null);
      setReason("");
      setSuspendDays("7");
    },
  });

  const unsuspend = useMutation({
    mutationFn: () => unsuspendUser(user.id, reason),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      qc.invalidateQueries({ queryKey: ["admin", "explain", user.id] });
      if (data.unsuspended) {
        toast.success("Suspension lifted");
      } else {
        toast.info("User was not suspended");
      }
      setConfirming(null);
      setReason("");
    },
  });

  const dossier = useMutation({
    mutationFn: () => downloadDossier(user.id, reason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      toast.success("Dossier downloaded");
      setConfirming(null);
      setReason("");
    },
  });

  const ban = useMutation({
    mutationFn: () => banUser(user.id, reason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      qc.invalidateQueries({ queryKey: ["admin", "explain", user.id] });
      toast.success("Banned");
      setConfirming(null);
      setReason("");
    },
  });

  const unban = useMutation({
    mutationFn: () => unbanUser(user.id, reason),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      qc.invalidateQueries({ queryKey: ["admin", "explain", user.id] });
      if (data.unbanned) {
        toast.success("Ban lifted");
      } else {
        toast.info("User was not banned");
      }
      setConfirming(null);
      setReason("");
    },
  });

  const emailChange = useMutation({
    mutationFn: () => changeUserEmail(user.id, newEmail, reason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      toast.success("Email changed");
      setNewEmail("");
      setConfirming(null);
      setReason("");
    },
  });

  const disableTotp = useMutation({
    mutationFn: () => disableUserTotp(user.id, reason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      toast.success("TOTP disabled");
      setConfirming(null);
      setReason("");
    },
  });

  const verifyEmail = useMutation({
    mutationFn: () => verifyUserEmail(user.id, reason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      qc.invalidateQueries({ queryKey: ["admin", "audit"] });
      toast.success("Email verified");
      setConfirming(null);
      setReason("");
    },
  });

  const isPending =
    resetPw.isPending ||
    emailChange.isPending ||
    disableTotp.isPending ||
    verifyEmail.isPending ||
    resetSafety.isPending ||
    bypassPending.isPending ||
    rotateKeys.isPending ||
    suspend.isPending ||
    unsuspend.isPending ||
    dossier.isPending ||
    ban.isPending ||
    unban.isPending;

  const isSuspended = user.account_status === "suspended";
  const isBanned = user.account_status === "banned";

  // The generated password is shown once for the admin to hand off. Don't
  // leave it sitting in the DOM indefinitely if the row stays expanded.
  useEffect(() => {
    if (!generatedPassword) return;
    const timer = setTimeout(() => setGeneratedPassword(null), 120_000);
    return () => clearTimeout(timer);
  }, [generatedPassword]);

  function copyPassword() {
    if (generatedPassword) {
      navigator.clipboard.writeText(generatedPassword);
      toast.success("Password copied");
    }
  }

  return (
    <div className="space-y-3 py-2">
      <ExplainPanel userId={user.id} />

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
            <Input
              className="h-7 w-48 text-xs"
              placeholder="Reason (required)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <Button
              size="sm"
              variant="destructive"
              className="h-7 text-xs"
              onClick={() => resetPw.mutate()}
              disabled={isPending || !reason.trim()}
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
            <Input
              className="h-7 w-48 text-xs"
              placeholder="Reason (required)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <Button
              size="sm"
              variant="destructive"
              className="h-7 text-xs"
              onClick={() => emailChange.mutate()}
              disabled={isPending || !newEmail || !reason.trim()}
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
              <Input
                className="h-7 w-48 text-xs"
                placeholder="Reason (required)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <Button
                size="sm"
                variant="destructive"
                className="h-7 text-xs"
                onClick={() => disableTotp.mutate()}
                disabled={isPending || !reason.trim()}
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

        {/* Reset System Safety (clear all safeguards going forward) */}
        {confirming === "reset-safety" ? (
          <div className="flex items-center gap-2">
            <Input
              className="h-7 w-64 text-xs"
              placeholder="Reason (e.g. support ticket #123)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <Button
              size="sm"
              variant="destructive"
              className="h-7 text-xs"
              onClick={() => resetSafety.mutate()}
              disabled={isPending || !reason.trim()}
            >
              Confirm
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => {
                setConfirming(null);
                setReason("");
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
            onClick={() => setConfirming("reset-safety")}
            title="Clear all System Safety toggles + zero grace period"
          >
            Reset safety
          </Button>
        )}

        {/* Bypass pending (drain queued pending_actions now) */}
        {confirming === "bypass-pending" ? (
          <div className="flex items-center gap-2">
            <Input
              className="h-7 w-64 text-xs"
              placeholder="Reason (e.g. support ticket #123)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <Button
              size="sm"
              variant="destructive"
              className="h-7 text-xs"
              onClick={() => bypassPending.mutate()}
              disabled={isPending || !reason.trim()}
            >
              Confirm
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => {
                setConfirming(null);
                setReason("");
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
            onClick={() => setConfirming("bypass-pending")}
            title="Finalize all queued pending actions immediately"
          >
            Drain pending
          </Button>
        )}

        {/* Force-rotate API keys */}
        {confirming === "rotate-keys" ? (
          <div className="flex items-center gap-2">
            <Input
              className="h-7 w-64 text-xs"
              placeholder="Reason (e.g. user reported key leak)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <Button
              size="sm"
              variant="destructive"
              className="h-7 text-xs"
              onClick={() => rotateKeys.mutate()}
              disabled={isPending || !reason.trim()}
            >
              Confirm
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => {
                setConfirming(null);
                setReason("");
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
            onClick={() => setConfirming("rotate-keys")}
            title="Revoke every API key on this account"
          >
            Rotate API keys
          </Button>
        )}

        {/* Suspend / unsuspend. The two are mutually exclusive — show
            unsuspend only when the user is currently suspended, and
            hide suspend in that case to avoid double-action confusion. */}
        {!isSuspended && !user.is_admin && (
          confirming === "suspend" ? (
            <div className="flex items-center gap-2">
              <Input
                className="h-7 w-44 text-xs"
                placeholder="Reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <Input
                className="h-7 w-16 text-xs"
                placeholder="days"
                value={suspendDays}
                onChange={(e) => setSuspendDays(e.target.value)}
                type="number"
                min={1}
                max={1825}
                title="Empty = indefinite (manual unsuspend required)"
              />
              <Button
                size="sm"
                variant="destructive"
                className="h-7 text-xs"
                onClick={() => suspend.mutate()}
                disabled={isPending || !reason.trim()}
              >
                Confirm
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => {
                  setConfirming(null);
                  setReason("");
                  setSuspendDays("7");
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
              onClick={() => setConfirming("suspend")}
              title="Soft-ban this account (auto-restores at expiry; leave duration blank for indefinite)"
            >
              Suspend
            </Button>
          )
        )}
        {isSuspended && (
          confirming === "unsuspend" ? (
            <div className="flex items-center gap-2">
              <Input
                className="h-7 w-44 text-xs"
                placeholder="Reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <Button
                size="sm"
                className="h-7 text-xs"
                onClick={() => unsuspend.mutate()}
                disabled={isPending || !reason.trim()}
              >
                Confirm
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => {
                  setConfirming(null);
                  setReason("");
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
              onClick={() => setConfirming("unsuspend")}
              title={
                user.suspended_reason
                  ? `Currently suspended: ${user.suspended_reason}`
                  : "Lift suspension early"
              }
            >
              Unsuspend
            </Button>
          )
        )}

        {/* Ban / unban. Permanent companion to suspend; no auto-restore. */}
        {!isBanned && !isSuspended && !user.is_admin && (
          confirming === "ban" ? (
            <div className="flex items-center gap-2">
              <Input
                className="h-7 w-64 text-xs"
                placeholder="Reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <Button
                size="sm"
                variant="destructive"
                className="h-7 text-xs"
                onClick={() => ban.mutate()}
                disabled={isPending || !reason.trim()}
              >
                Confirm
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => {
                  setConfirming(null);
                  setReason("");
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
              className="h-7 text-xs text-destructive hover:text-destructive"
              onClick={() => setConfirming("ban")}
              title="Permanent ban (no auto-restore; /unban to lift)"
            >
              Ban
            </Button>
          )
        )}
        {isBanned && (
          confirming === "unban" ? (
            <div className="flex items-center gap-2">
              <Input
                className="h-7 w-44 text-xs"
                placeholder="Reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <Button
                size="sm"
                className="h-7 text-xs"
                onClick={() => unban.mutate()}
                disabled={isPending || !reason.trim()}
              >
                Confirm
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => {
                  setConfirming(null);
                  setReason("");
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
              onClick={() => setConfirming("unban")}
              title="Lift permanent ban"
            >
              Unban
            </Button>
          )
        )}

        {/* Dossier export (GDPR Article 15 metadata bundle) */}
        {confirming === "dossier" ? (
          <div className="flex items-center gap-2">
            <Input
              className="h-7 w-64 text-xs"
              placeholder="Reason (e.g. DSAR request)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <Button
              size="sm"
              className="h-7 text-xs"
              onClick={() => dossier.mutate()}
              disabled={isPending || !reason.trim()}
            >
              Download
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => {
                setConfirming(null);
                setReason("");
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
            onClick={() => setConfirming("dossier")}
            title="Download GDPR Article 15 metadata bundle"
          >
            Dossier
          </Button>
        )}

        {/* Verify email */}
        {!user.email_verified && (
          confirming === "verify-email" ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">
                Mark email verified?
              </span>
              <Input
                className="h-7 w-48 text-xs"
                placeholder="Reason (required)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <Button
                size="sm"
                className="h-7 text-xs"
                onClick={() => verifyEmail.mutate()}
                disabled={isPending || !reason.trim()}
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
  const [canUploadImages, setCanUploadImages] = useState(user.can_upload_images);
  const [canUploadAnimated, setCanUploadAnimated] = useState(
    user.can_upload_animated_images,
  );
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
    mutation.mutate({
      tier,
      is_admin: isAdmin,
      member_limit: limit,
      can_upload_images: canUploadImages,
      can_upload_animated_images: canUploadAnimated,
    });
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
            {user.account_status === "suspended" && (
              <Badge
                variant="destructive"
                className="text-[10px] px-1 py-0"
                title={
                  user.suspended_reason
                    ? `Reason: ${user.suspended_reason}` +
                      (user.suspended_until
                        ? ` (until ${new Date(user.suspended_until).toLocaleString()})`
                        : " (indefinite)")
                    : "Suspended"
                }
              >
                Suspended
              </Badge>
            )}
            {user.account_status === "banned" && (
              <Badge
                variant="destructive"
                className="text-[10px] px-1 py-0"
              >
                Banned
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
        <td className="py-2 pr-4">
          <Select
            value={canUploadImages ? "yes" : "no"}
            onValueChange={(v) => {
              setCanUploadImages(v === "yes");
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
        <td className="py-2 pr-4">
          <Select
            value={canUploadAnimated ? "yes" : "no"}
            onValueChange={(v) => {
              setCanUploadAnimated(v === "yes");
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
          <td colSpan={10} className="px-6 pb-3">
            <UserActions user={user} />
          </td>
        </tr>
      )}
    </>
  );
}

export function AdminUsersPage() {
  const [search, setSearch] = useState("");
  const [signupIp, setSignupIp] = useState("");
  const [page, setPage] = useState(1);

  const { data: users } = useQuery({
    queryKey: ["admin", "users", search, signupIp, page],
    queryFn: () =>
      getAdminUsers(search || undefined, page, 50, signupIp || undefined),
  });

  return (
    <>
      <PageHeader title="Users" />
      <div className="max-w-5xl space-y-4">
        <div className="flex flex-wrap gap-2">
          <Input
            placeholder="Search by email..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            className="max-w-xs"
          />
          <Input
            placeholder="Filter by signup IP (exact)..."
            value={signupIp}
            onChange={(e) => {
              setSignupIp(e.target.value);
              setPage(1);
            }}
            className="max-w-xs font-mono text-xs"
          />
        </div>
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
                  <th className="py-2 pr-4 text-left font-medium">Uploads</th>
                  <th className="py-2 pr-4 text-left font-medium">Animated</th>
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
                      colSpan={10}
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
