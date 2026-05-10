import type { DestinationType } from "@/types/api";

const labels: Record<DestinationType, string> = {
  web_push: "Web push",
  webhook: "Webhook",
  ntfy: "ntfy",
  pushover: "Pushover",
  fcm: "Android push",
  apns_dev: "iOS push (dev)",
  apns_prod: "iOS push",
};

export function destinationLabel(type: DestinationType): string {
  return labels[type] ?? type;
}
