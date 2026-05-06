import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getMySystem, updateMySystem } from "@/lib/systems";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import type { SystemUpdate } from "@/types/api";
import { toast } from "sonner";

export function FrontPreferencesCard() {
  const qc = useQueryClient();
  const { data: system } = useQuery({ queryKey: ["system", "me"], queryFn: getMySystem });
  const update = useMutation({
    mutationFn: (patch: SystemUpdate) => updateMySystem(patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "me"] });
      qc.invalidateQueries({ queryKey: ["fronts"] });
      toast.success("Front preferences saved");
    },
  });

  if (!system) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Fronting</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-start gap-3">
          <Checkbox
            id="replace-fronts-default"
            checked={system.replace_fronts_default}
            onCheckedChange={(v) =>
              update.mutate({ replace_fronts_default: v === true })
            }
            disabled={update.isPending}
          />
          <div>
            <Label htmlFor="replace-fronts-default" className="text-sm font-medium cursor-pointer">
              End current fronts when starting a new one
            </Label>
            <p className="text-xs text-muted-foreground mt-0.5">
              This is the default for the "End all current fronts" checkbox in the start front dialog.
              You can override it per front.
            </p>
          </div>
        </div>

        <div className="flex items-start gap-3">
          <Checkbox
            id="coalesce-contiguous-fronts"
            checked={system.coalesce_contiguous_fronts}
            onCheckedChange={(v) =>
              update.mutate({ coalesce_contiguous_fronts: v === true })
            }
            disabled={update.isPending}
          />
          <div>
            <Label
              htmlFor="coalesce-contiguous-fronts"
              className="text-sm font-medium cursor-pointer"
            >
              Coalesce contiguous fronting
            </Label>
            <p className="text-xs text-muted-foreground mt-0.5">
              Show "fronting since" per member instead of per front entry.
              When a member is in a chain of back-to-back front entries
              (e.g. solo &rarr; cofront), their timer reflects the
              earliest entry rather than resetting on each one.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
