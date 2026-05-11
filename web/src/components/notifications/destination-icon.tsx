import type { ComponentType } from "react";
import { Apple, Bell, Globe, MessageSquare, Smartphone, Webhook } from "lucide-react";

import type { DestinationType } from "@/types/api";

const icons: Record<DestinationType, ComponentType<{ className?: string }>> = {
  web_push: Bell,
  webhook: Webhook,
  ntfy: MessageSquare,
  pushover: Globe,
  fcm: Smartphone,
  apns_dev: Apple,
  apns_prod: Apple,
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
