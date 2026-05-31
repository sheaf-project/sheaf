import { Monitor, Moon, Sun } from "lucide-react";

import { useTheme, type ThemeMode } from "@/hooks/use-theme";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Tri-state mode toggle used in the sidebar header and the
 * pre-login pages. Cycles dark -> system -> light -> dark.
 *
 * Distinct from the Appearance settings card, which is the canonical
 * place to set both mode and palette. This is the inline quick-access
 * version: it changes mode only, never palette. When the user reaches
 * the system position, the icon switches to `Monitor` and the
 * tooltip explains that the OS-following state is active.
 *
 * Cycle order chosen so the most common transition (dark -> light or
 * vice versa, with a single click) lands on the system state in
 * between rather than past it: someone who clicks once from dark
 * ends up on system (which often appears as dark anyway depending on
 * OS), and a second click lands on light. Avoids the "wait, why is
 * it still dark" confusion.
 */

const ICON: Record<ThemeMode, typeof Sun> = {
  dark: Moon,
  system: Monitor,
  light: Sun,
};

const NEXT: Record<ThemeMode, ThemeMode> = {
  dark: "system",
  system: "light",
  light: "dark",
};

const LABEL: Record<ThemeMode, string> = {
  dark: "Theme: dark (click for system)",
  system: "Theme: system (click for light)",
  light: "Theme: light (click for dark)",
};

export function ThemeModeToggle({ className }: { className?: string }) {
  const { mode, setMode } = useTheme();
  const Icon = ICON[mode];
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={() => setMode(NEXT[mode])}
      title={LABEL[mode]}
      aria-label={LABEL[mode]}
      className={cn("h-8 w-8", className)}
    >
      <Icon className="h-4 w-4" />
    </Button>
  );
}
