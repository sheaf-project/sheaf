import type { ComponentType } from "react";
import { Bell, Globe, MessageSquare, Smartphone, Webhook } from "lucide-react";

import type { DestinationType } from "@/types/api";

const icons: Record<DestinationType, ComponentType<{ className?: string }>> = {
  web_push: Bell,
  webhook: Webhook,
  ntfy: MessageSquare,
  pushover: Globe,
  mobile_push: Smartphone,
  // Legacy mobile types — same icon since the recipient experience is
  // identical now.
  fcm: Smartphone,
  apns_dev: Smartphone,
  apns_prod: Smartphone,
};

export function DestinationIcon({
  type,
  className,
}: {
  type: DestinationType;
  className?: string;
}) {
  const Icon = icons[type] ?? Bell;
  return <Icon className={className} />;
}
