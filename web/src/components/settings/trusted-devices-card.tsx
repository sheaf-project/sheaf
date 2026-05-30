import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getTrustedDevices, renameTrustedDevice, revokeTrustedDevice, revokeAllTrustedDevices, type TrustedDevice } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { timeAgo } from "@/lib/utils";
import { Pencil } from "lucide-react";
import { toast } from "sonner";

export function TrustedDevicesCard() {
  const qc = useQueryClient();
  const { data: devices } = useQuery({
    queryKey: ["trusted-devices"],
    queryFn: getTrustedDevices,
  });
  const revoke = useMutation({
    mutationFn: revokeTrustedDevice,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trusted-devices"] });
      toast.success("Device revoked");
    },
  });
  const revokeAll = useMutation({
    mutationFn: revokeAllTrustedDevices,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trusted-devices"] });
      toast.success("All trusted devices revoked");
    },
  });
  const renameMut = useMutation({
    mutationFn: ({ id, nickname }: { id: string; nickname: string }) =>
      renameTrustedDevice(id, nickname),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trusted-devices"] });
      toast.success("Device renamed");
    },
  });

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editNickname, setEditNickname] = useState("");

  function startEdit(d: TrustedDevice) {
    setEditingId(d.id);
    setEditNickname(d.nickname ?? "");
  }

  function saveNickname() {
    if (editingId) {
      renameMut.mutate({ id: editingId, nickname: editNickname });
      setEditingId(null);
    }
  }

  if (!devices) return null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">Trusted devices</CardTitle>
        {devices.length > 0 && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => revokeAll.mutate()}
            disabled={revokeAll.isPending}
          >
            {revokeAll.isPending ? "Revoking..." : "Revoke all"}
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Browsers that can skip the 2FA prompt for 30 days. Revoked
          automatically when you change your password or disable 2FA.
        </p>
        {devices.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No trusted devices.
          </p>
        )}
        {devices.map((d) => (
          <div
            key={d.id}
            className="flex items-start justify-between rounded-md border px-3 py-2 text-sm"
          >
            <div className="space-y-1 min-w-0 flex-1">
              <div className="flex items-center gap-2">
                {editingId === d.id ? (
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
                      placeholder="Device name"
                      autoFocus
                      onBlur={saveNickname}
                    />
                  </form>
                ) : (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 font-medium hover:text-muted-foreground transition-colors"
                    onClick={() => startEdit(d)}
                  >
                    {d.nickname || d.client_name || "Device"}
                    <Pencil className="h-3 w-3 text-muted-foreground" />
                  </button>
                )}
                {d.is_current && (
                  <Badge variant="outline" className="text-xs">
                    This browser
                  </Badge>
                )}
              </div>
              {/* Always show client_name + user_agent as the secondary
                  line. When the device has a custom nickname, this is
                  the only place the actual client/UA shows; when it
                  doesn't, the nickname slot above already shows
                  client_name, but the UA detail below remains useful
                  for telling apart e.g. two Firefox profiles. */}
              {editingId !== d.id && (
                <p className="text-xs text-muted-foreground truncate">
                  {d.nickname && d.client_name
                    ? `${d.client_name} · ${d.user_agent}`
                    : d.user_agent}
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                {d.last_used_at
                  ? `Last used ${timeAgo(d.last_used_at)}`
                  : "Not yet used"}
                {d.last_used_ip && ` from ${d.last_used_ip}`}
                {" · "}Expires {new Date(d.expires_at).toLocaleDateString()}
              </p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive shrink-0"
              onClick={() => revoke.mutate(d.id)}
              disabled={revoke.isPending}
            >
              Revoke
            </Button>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
