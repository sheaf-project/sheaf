import { type FormEvent, useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/hooks/use-auth";
import { listApiKeys, createApiKey, revokeApiKey } from "@/lib/api-keys";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import type { ApiKey, ApiKeyCreated } from "@/types/api";
import { toast } from "sonner";

interface ScopeResource {
  key: string;
  label: string;
  hasDelete?: boolean;
  readOnly?: boolean;
}

const SCOPE_RESOURCES: ScopeResource[] = [
  { key: "system", label: "System" },
  { key: "members", label: "Members", hasDelete: true },
  { key: "fronts", label: "Fronts", hasDelete: true },
  { key: "groups", label: "Groups", hasDelete: true },
  { key: "tags", label: "Tags", hasDelete: true },
  { key: "fields", label: "Custom fields", hasDelete: true },
  { key: "export", label: "Data export", readOnly: true },
];

type ScopeLevel = "none" | "read" | "write" | "write+delete";

function scopesFromLevels(levels: Record<string, ScopeLevel>, isAdmin: boolean, adminLevel: ScopeLevel): string[] {
  const scopes: string[] = [];
  for (const { key, readOnly, hasDelete } of SCOPE_RESOURCES) {
    const level = levels[key] ?? "none";
    if (level === "none") continue;
    if (readOnly) {
      scopes.push(`${key}:read`);
    } else if (level === "read") {
      scopes.push(`${key}:read`);
    } else if (level === "write") {
      scopes.push(`${key}:write`);
    } else if (level === "write+delete" && hasDelete) {
      scopes.push(`${key}:write`, `${key}:delete`);
    }
  }
  if (isAdmin && adminLevel !== "none") {
    scopes.push(adminLevel === "write" ? "admin:write" : "admin:read");
  }
  return scopes;
}

function ScopeRow({
  label,
  value,
  onChange,
  readOnly,
  hasDelete,
}: {
  label: string;
  value: ScopeLevel;
  onChange: (v: ScopeLevel) => void;
  readOnly?: boolean;
  hasDelete?: boolean;
}) {
  const options: { v: ScopeLevel; label: string }[] = [
    { v: "none", label: "None" },
    { v: "read", label: "Read" },
    ...(!readOnly ? [{ v: "write" as ScopeLevel, label: "Read+Write" }] : []),
    ...(hasDelete ? [{ v: "write+delete" as ScopeLevel, label: "Write+Delete" }] : []),
  ];
  return (
    <div className="flex items-center justify-between py-1.5 text-sm">
      <span className="w-32 text-muted-foreground">{label}</span>
      <div className="flex gap-1">
        {options.map((o) => (
          <button
            key={o.v}
            type="button"
            onClick={() => onChange(o.v)}
            className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
              value === o.v
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:bg-muted/80"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function ApiKeysCard() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const { data: keys } = useQuery({ queryKey: ["api-keys"], queryFn: listApiKeys });
  const revoke = useMutation({
    mutationFn: revokeApiKey,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["api-keys"] });
      toast.success("API key revoked");
    },
  });
  const create = useMutation({
    mutationFn: createApiKey,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys"] }),
  });

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [levels, setLevels] = useState<Record<string, ScopeLevel>>({});
  const [adminLevel, setAdminLevel] = useState<ScopeLevel>("none");
  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [revokeConfirmId, setRevokeConfirmId] = useState<string | null>(null);

  const setLevel = useCallback((key: string, v: ScopeLevel) => {
    setLevels((prev) => ({ ...prev, [key]: v }));
  }, []);

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    const scopes = scopesFromLevels(levels, !!user?.is_admin, adminLevel);
    create.mutate(
      { name, scopes },
      {
        onSuccess: (k) => {
          setCreatedKey(k);
          setShowForm(false);
          setName("");
          setLevels({});
          setAdminLevel("none");
        },
      },
    );
  }

  function handleCopy() {
    if (!createdKey) return;
    navigator.clipboard.writeText(createdKey.key);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">API keys</CardTitle>
        {!showForm && !createdKey && (
          <Button size="sm" variant="outline" onClick={() => setShowForm(true)}>
            New key
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {createdKey && (
          <div className="rounded-md border border-yellow-500/30 bg-yellow-500/5 p-3 space-y-2">
            <p className="text-sm font-medium text-yellow-700 dark:text-yellow-400">
              Copy this key now — it won't be shown again.
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 rounded bg-muted px-2 py-1.5 text-xs font-mono break-all">
                {createdKey.key}
              </code>
              <Button size="sm" variant="outline" onClick={handleCopy}>
                {copied ? "Copied!" : "Copy"}
              </Button>
            </div>
            <Button size="sm" variant="ghost" onClick={() => setCreatedKey(null)}>
              Done
            </Button>
          </div>
        )}

        {showForm && (
          <form onSubmit={handleCreate} className="space-y-4 rounded-md border p-4">
            <div className="space-y-1">
              <Label className="text-sm">Key name</Label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Mobile app, Scripts"
                required
              />
            </div>
            <div className="space-y-1">
              <Label className="text-sm">Scopes</Label>
              <div className="divide-y rounded-md border px-3">
                {SCOPE_RESOURCES.map(({ key, label, readOnly, hasDelete }) => (
                  <ScopeRow
                    key={key}
                    label={label}
                    value={levels[key] ?? "none"}
                    onChange={(v) => setLevel(key, v)}
                    readOnly={readOnly}
                    hasDelete={hasDelete}
                  />
                ))}
                {user?.is_admin && (
                  <>
                    <div className="py-1.5 text-xs text-muted-foreground font-medium">Admin</div>
                    <ScopeRow
                      label="Admin"
                      value={adminLevel}
                      onChange={setAdminLevel}
                    />
                  </>
                )}
              </div>
            </div>
            <div className="flex gap-2">
              <Button type="submit" size="sm" disabled={create.isPending || !name}>
                {create.isPending ? "Creating..." : "Create key"}
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setShowForm(false)}>
                Cancel
              </Button>
            </div>
          </form>
        )}

        {keys && keys.length > 0 ? (
          <div className="space-y-2">
            {keys.map((k: ApiKey) => (
              <div
                key={k.id}
                className="flex items-start justify-between rounded-md border px-3 py-2 text-sm"
              >
                <div className="space-y-1">
                  <p className="font-medium">{k.name}</p>
                  <div className="flex flex-wrap gap-1">
                    {k.scopes.map((s) => (
                      <Badge key={s} variant="outline" className="text-xs">
                        {s}
                      </Badge>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Created {new Date(k.created_at).toLocaleDateString()}
                    {k.last_used_at && ` · Last used ${new Date(k.last_used_at).toLocaleDateString()}`}
                    {k.expires_at && ` · Expires ${new Date(k.expires_at).toLocaleDateString()}`}
                  </p>
                </div>
                {revokeConfirmId === k.id ? (
                  <div className="flex items-center gap-1 shrink-0">
                    <Button
                      variant="destructive"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => {
                        revoke.mutate(k.id);
                        setRevokeConfirmId(null);
                      }}
                      disabled={revoke.isPending}
                    >
                      Confirm
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => setRevokeConfirmId(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                ) : (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-destructive hover:text-destructive shrink-0"
                    onClick={() => setRevokeConfirmId(k.id)}
                    disabled={revoke.isPending}
                  >
                    Revoke
                  </Button>
                )}
              </div>
            ))}
          </div>
        ) : (
          !showForm && !createdKey && (
            <p className="text-sm text-muted-foreground">No API keys yet.</p>
          )
        )}
      </CardContent>
    </Card>
  );
}
