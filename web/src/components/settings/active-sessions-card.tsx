import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getSessions, renameSession, revokeSession, revokeOtherSessions, type Session } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { timeAgo } from "@/lib/utils";
import { useDateFormatters } from "@/hooks/use-date-formatters";
import { Pencil } from "lucide-react";
import { toast } from "sonner";

export function ActiveSessionsCard() {
  const qc = useQueryClient();
  const { formatDate } = useDateFormatters();
  const { data: sessions } = useQuery({
    queryKey: ["sessions"],
    queryFn: getSessions,
  });
  const revoke = useMutation({
    mutationFn: revokeSession,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
      toast.success("Session revoked");
    },
  });
  const revokeAll = useMutation({
    mutationFn: revokeOtherSessions,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
      toast.success("Other sessions revoked");
    },
  });
  const renameMut = useMutation({
    mutationFn: ({ id, nickname }: { id: string; nickname: string }) =>
      renameSession(id, nickname),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
      toast.success("Session renamed");
    },
  });

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editNickname, setEditNickname] = useState("");

  function startEdit(s: Session) {
    setEditingId(s.id);
    setEditNickname(s.nickname ?? "");
  }

  function saveNickname() {
    if (editingId) {
      renameMut.mutate({ id: editingId, nickname: editNickname });
      setEditingId(null);
    }
  }

  const otherCount = sessions?.filter((s) => !s.is_current).length ?? 0;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">Active sessions</CardTitle>
        {otherCount > 0 && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => revokeAll.mutate()}
            disabled={revokeAll.isPending}
          >
            {revokeAll.isPending ? "Revoking..." : "Revoke all others"}
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {revokeAll.data && revokeAll.data.revoked > 0 && (
          <p className="text-xs text-green-600">
            Revoked {revokeAll.data.revoked} session{revokeAll.data.revoked !== 1 ? "s" : ""}
          </p>
        )}
        {sessions && sessions.length === 0 && (
          <p className="text-sm text-muted-foreground">No active sessions.</p>
        )}
        {sessions?.map((s) => (
          <div
            key={s.id}
            className="flex items-start justify-between rounded-md border px-3 py-2 text-sm"
          >
            <div className="space-y-1 min-w-0 flex-1">
              <div className="flex items-center gap-2">
                {editingId === s.id ? (
                  <form
                    className="flex items-center gap-1"
                    onSubmit={(e) => {
                      e.preventDefault();
                      saveNickname();
                    }}
                  >
                    <Input
                      value={editNickname}
                      onChange={(e) => setEditNickname(e.target.value)}
                      className="h-6 w-40 text-xs"
                      placeholder="Session name"
                      autoFocus
                      onBlur={saveNickname}
                    />
                  </form>
                ) : (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 font-medium hover:text-muted-foreground transition-colors"
                    onClick={() => startEdit(s)}
                  >
                    {s.nickname || s.client_name}
                    <Pencil className="h-3 w-3 text-muted-foreground" />
                  </button>
                )}
                {s.is_current && (
                  <Badge variant="outline" className="text-xs">
                    Current
                  </Badge>
                )}
              </div>
              {s.nickname && editingId !== s.id && (
                <p className="text-xs text-muted-foreground">{s.client_name}</p>
              )}
              <p className="text-xs text-muted-foreground">
                Last active {timeAgo(s.last_active_at)}
                {s.last_active_ip && ` from ${s.last_active_ip}`}
                {" · "}Created {formatDate(s.created_at)}
                {s.created_ip && ` from ${s.created_ip}`}
              </p>
            </div>
            {!s.is_current && (
              <Button
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive shrink-0"
                onClick={() => revoke.mutate(s.id)}
                disabled={revoke.isPending}
              >
                Revoke
              </Button>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
