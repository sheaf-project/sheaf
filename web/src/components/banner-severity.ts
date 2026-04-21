import { Info, AlertTriangle, AlertOctagon } from "lucide-react";

export type BannerSeverity = "info" | "warning" | "critical";

export const severityConfig = {
  info: {
    icon: Info,
    bg: "bg-blue-500/10",
    border: "border-blue-500/20",
    text: "text-blue-700 dark:text-blue-300",
    iconColor: "text-blue-500",
  },
  warning: {
    icon: AlertTriangle,
    bg: "bg-yellow-500/10",
    border: "border-yellow-500/20",
    text: "text-yellow-800 dark:text-yellow-200",
    iconColor: "text-yellow-500",
  },
  critical: {
    icon: AlertOctagon,
    bg: "bg-destructive/10",
    border: "border-destructive/20",
    text: "text-destructive",
    iconColor: "text-destructive",
  },
} as const;
