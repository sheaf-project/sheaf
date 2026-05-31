import { Check, Monitor, Moon, Sun } from "lucide-react";

import { useTheme, type ThemeMode } from "@/hooks/use-theme";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { PALETTES, type PaletteId } from "@/lib/palettes";
import { cn } from "@/lib/utils";

/**
 * Single source of truth for picking the theme: palette grid + mode
 * radio + sync-across-devices toggle. The sync toggle decides where
 * the picks get persisted (backend client_settings/web if on,
 * localStorage if off); the picker is otherwise the same in either
 * mode so users don't have to learn two flows.
 *
 * Each palette card shows a three-colour swatch sampled from the
 * palette's actual primary / secondary / accent values, rendered in
 * the mode the user currently has applied (so they're previewing
 * "what does this look like right now" rather than a fixed light or
 * dark sample).
 */
export function AppearanceCard() {
  const { mode, effectiveMode, palette, synced, setMode, setPalette, setSynced } =
    useTheme();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Appearance</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <PaletteGrid
          selected={palette}
          previewMode={effectiveMode}
          onSelect={setPalette}
        />
        <ModeRadio mode={mode} onChange={setMode} />
        <SyncToggle synced={synced} onChange={setSynced} />
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Palette grid
// ---------------------------------------------------------------------------

function PaletteGrid({
  selected,
  previewMode,
  onSelect,
}: {
  selected: PaletteId;
  previewMode: "light" | "dark";
  onSelect: (id: PaletteId) => void;
}) {
  return (
    <div className="space-y-2">
      <Label className="text-sm font-medium">Palette</Label>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {PALETTES.map((p) => {
          const swatch = p.swatch[previewMode];
          const isSelected = p.id === selected;
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => onSelect(p.id)}
              className={cn(
                "relative flex flex-col items-stretch gap-2 rounded-md border p-3 text-left transition-colors hover:bg-accent/40",
                isSelected && "border-primary ring-2 ring-primary/30",
              )}
              aria-pressed={isSelected}
            >
              <div className="flex h-6 overflow-hidden rounded">
                <span
                  className="flex-1"
                  style={{ backgroundColor: swatch[0] }}
                  aria-hidden="true"
                />
                <span
                  className="flex-1"
                  style={{ backgroundColor: swatch[1] }}
                  aria-hidden="true"
                />
                <span
                  className="flex-1"
                  style={{ backgroundColor: swatch[2] }}
                  aria-hidden="true"
                />
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">{p.displayName}</span>
                {isSelected && (
                  <Check
                    className="h-3.5 w-3.5 text-primary"
                    aria-label="Selected"
                  />
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mode radio (dark / system / light)
// ---------------------------------------------------------------------------

const MODE_OPTIONS: { value: ThemeMode; label: string; icon: typeof Sun }[] = [
  { value: "system", label: "System", icon: Monitor },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "light", label: "Light", icon: Sun },
];

function ModeRadio({
  mode,
  onChange,
}: {
  mode: ThemeMode;
  onChange: (mode: ThemeMode) => void;
}) {
  return (
    <div className="space-y-2">
      <Label className="text-sm font-medium">Theme mode</Label>
      <div className="grid grid-cols-3 gap-2" role="radiogroup">
        {MODE_OPTIONS.map((opt) => {
          const Icon = opt.icon;
          const isSelected = opt.value === mode;
          return (
            <button
              key={opt.value}
              type="button"
              role="radio"
              aria-checked={isSelected}
              onClick={() => onChange(opt.value)}
              className={cn(
                "flex flex-col items-center gap-1 rounded-md border px-3 py-2 text-xs font-medium transition-colors hover:bg-accent/40",
                isSelected && "border-primary ring-2 ring-primary/30",
              )}
            >
              <Icon className="h-4 w-4" />
              <span>{opt.label}</span>
            </button>
          );
        })}
      </div>
      <p className="text-xs text-muted-foreground">
        System follows your operating system's dark / light preference.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sync toggle
// ---------------------------------------------------------------------------

function SyncToggle({
  synced,
  onChange,
}: {
  synced: boolean;
  onChange: (synced: boolean) => void;
}) {
  return (
    <div className="flex items-start gap-3 border-t pt-4">
      <Checkbox
        id="theme-sync"
        checked={synced}
        onCheckedChange={(v) => onChange(v === true)}
      />
      <div className="space-y-0.5">
        <Label
          htmlFor="theme-sync"
          className="text-sm font-medium cursor-pointer"
        >
          Sync these settings across my devices
        </Label>
        <p className="text-xs text-muted-foreground">
          {synced
            ? "Your palette and mode follow your account. Changes here apply to every browser logged into this account."
            : "This browser keeps its own palette and mode. Other browsers logged into this account use their own picks (or your last synced choice)."}
        </p>
      </div>
    </div>
  );
}
