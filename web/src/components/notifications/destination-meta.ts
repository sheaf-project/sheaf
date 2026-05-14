import type { DestinationType } from "@/types/api";

const labels: Record<DestinationType, string> = {
  web_push: "Web push",
  webhook: "Webhook",
  ntfy: "ntfy",
  pushover: "Pushover",
  mobile_push: "Mobile push",
  // Legacy labels — still rendered if a pre-migration row leaks through.
  fcm: "Mobile push (Android, legacy)",
  apns_dev: "Mobile push (iOS dev, legacy)",
  apns_prod: "Mobile push (iOS, legacy)",
};

export function destinationLabel(type: DestinationType): string {
  return labels[type] ?? type;
}
