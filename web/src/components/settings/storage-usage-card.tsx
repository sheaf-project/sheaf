import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getStorageUsage, cleanupFiles } from "@/lib/files";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatBytes } from "@/lib/utils";
import { toast } from "sonner";

export function StorageUsageCard() {
  const qc = useQueryClient();
  const { data: usage } = useQuery({
    queryKey: ["storage", "usage"],
    queryFn: getStorageUsage,
  });
  const cleanup = useMutation({
    mutationFn: cleanupFiles,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["storage", "usage"] });
      if (data?.orphaned > 0) {
        toast.success(`Cleaned up ${data.orphaned} orphaned file(s)`);
      } else {
        toast.success("No orphaned files found");
      }
    },
  });

  if (!usage) return null;

  const unlimited = usage.quota_bytes === 0;
  const percent = unlimited ? 0 : Math.round((usage.used_bytes / usage.quota_bytes) * 100);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Storage</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1">
          <div className="flex justify-between text-sm">
            <span>{formatBytes(usage.used_bytes)} used</span>
            <span className="text-muted-foreground">
              {unlimited ? "Unlimited" : formatBytes(usage.quota_bytes)}
            </span>
          </div>
          {!unlimited && (
            <div className="h-2 rounded-full bg-muted overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  percent > 90 ? "bg-red-500" : percent > 70 ? "bg-yellow-500" : "bg-green-500"
                }`}
                style={{ width: `${Math.min(percent, 100)}%` }}
              />
            </div>
          )}
        </div>
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            Remove unused uploaded images to free space
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => cleanup.mutate()}
            disabled={cleanup.isPending}
          >
            {cleanup.isPending ? "Cleaning..." : "Clean up"}
          </Button>
        </div>
        {cleanup.data && cleanup.data.orphaned > 0 && (
          <p className="text-xs text-green-600">
            Removed {cleanup.data.orphaned} file{cleanup.data.orphaned !== 1 ? "s" : ""}, freed {formatBytes(cleanup.data.freed_bytes)}
          </p>
        )}
        {cleanup.data && cleanup.data.orphaned === 0 && (
          <p className="text-xs text-muted-foreground">No orphaned files found</p>
        )}
      </CardContent>
    </Card>
  );
}
