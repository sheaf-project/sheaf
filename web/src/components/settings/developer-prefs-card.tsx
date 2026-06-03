import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { useDeveloperPrefs } from "@/hooks/use-developer-prefs";

export function DeveloperPrefsCard() {
  const { showTechnicalErrors, setShowTechnicalErrors } = useDeveloperPrefs();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Error reporting</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-start gap-3">
          <Checkbox
            id="show-technical-errors"
            checked={showTechnicalErrors}
            onCheckedChange={(v) => setShowTechnicalErrors(v === true)}
          />
          <div>
            <Label
              htmlFor="show-technical-errors"
              className="text-sm font-medium cursor-pointer"
            >
              Show technical error details
            </Label>
            <p className="text-xs text-muted-foreground mt-0.5">
              When something goes wrong, show the HTTP status code and the
              server's exact error message instead of a friendly summary.
              Useful for reporting bugs; can be noisy day-to-day.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
