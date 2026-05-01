import type { DestinationType } from "@/types/api";

const labels: Record<DestinationType, string> = {
  web_push: "Web push",
  webhook: "Webhook",
  ntfy: "ntfy",
  pushover: "Pushover",
};

export function destinationLabel(type: DestinationType): string {
  return labels[type] ?? type;
}
