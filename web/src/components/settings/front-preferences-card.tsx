import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getMySystem, updateMySystem } from "@/lib/systems";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";

export function FrontPreferencesCard() {
  const qc = useQueryClient();
  const { data: system } = useQuery({ queryKey: ["system", "me"], queryFn: getMySystem });
  const update = useMutation({
    mutationFn: (replace_fronts_default: boolean) => updateMySystem({ replace_fronts_default }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "me"] });
      toast.success("Front preferences saved");
    },
  });

  if (!system) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Fronting</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-start gap-3">
          <Checkbox
            id="replace-fronts-default"
            checked={system.replace_fronts_default}
            onCheckedChange={(v) => update.mutate(v === true)}
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
      </CardContent>
    </Card>
  );
}
