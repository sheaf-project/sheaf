import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listClientSettings, deleteClientSettings } from "@/lib/client-settings";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "sonner";

export function ClientSettingsCard() {
  const qc = useQueryClient();
  const { data: entries, isLoading } = useQuery({
    queryKey: ["client-settings"],
    queryFn: listClientSettings,
  });

  const deleteMut = useMutation({
    mutationFn: deleteClientSettings,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["client-settings"] });
      toast.success("Client settings deleted");
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Client Settings</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground mb-3">
          Settings stored by apps and integrations that use this account.
        </p>
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading...</p>
        )}
        {entries && entries.length === 0 && (
          <p className="text-sm text-muted-foreground">No client settings stored.</p>
        )}
        {entries && entries.length > 0 && (
          <div className="space-y-2">
            {entries.map((entry) => (
              <div
                key={entry.client_id}
                className="flex items-center justify-between rounded-md border px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium">{entry.client_id}</p>
                  <p className="text-xs text-muted-foreground">
                    {JSON.stringify(entry.settings).length.toLocaleString()} bytes
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:text-destructive shrink-0"
                  onClick={() => deleteMut.mutate(entry.client_id)}
                  disabled={deleteMut.isPending}
                >
                  Delete
                </Button>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
