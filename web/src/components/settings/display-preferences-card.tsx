import { useShowImageBadges } from "@/hooks/use-preferences";
import { useUiScale } from "@/hooks/use-theme";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function DisplayPreferencesCard() {
  const [showBadges, setShowBadges] = useShowImageBadges();
  const { scale, setScale, scales } = useUiScale();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Display</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">UI scale</p>
            <p className="text-xs text-muted-foreground">
              Adjust the interface size
            </p>
          </div>
          <div className="flex gap-1">
            {scales.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setScale(s)}
                className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
                  scale === s
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground hover:bg-muted/80"
                }`}
              >
                {s}%
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">Image source badges</p>
            <p className="text-xs text-muted-foreground">
              Show hosted/external labels on images in bios
            </p>
          </div>
          <Button
            variant={showBadges ? "default" : "outline"}
            size="sm"
            onClick={() => setShowBadges(!showBadges)}
          >
            {showBadges ? "On" : "Off"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
