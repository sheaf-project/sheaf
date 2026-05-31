import { useTheme } from "@/hooks/use-theme";
import { cn } from "@/lib/utils";

export function Logo({ className }: { className?: string }) {
  const { effectiveMode } = useTheme();
  const src = effectiveMode === "dark" ? "/logo-dark.svg" : "/logo-light.svg";
  return <img src={src} alt="Sheaf" className={cn("select-none", className)} />;
}
