import type { ComponentType } from "react";
import { Bell, Globe, MessageSquare, Webhook } from "lucide-react";

import type { DestinationType } from "@/types/api";

const icons: Record<DestinationType, ComponentType<{ className?: string }>> = {
  web_push: Bell,
  webhook: Webhook,
  ntfy: MessageSquare,
  pushover: Globe,
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
