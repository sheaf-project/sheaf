import { apiFetch } from "./api-client";
import type { FrontingAnalytics } from "@/types/api";

export async function getFrontingAnalytics(opts?: {
  since?: Date;
  until?: Date;
  tz?: string;
}): Promise<FrontingAnalytics> {
  const params = new URLSearchParams();
  if (opts?.since) params.set("since", opts.since.toISOString());
  if (opts?.until) params.set("until", opts.until.toISOString());
  if (opts?.tz) params.set("tz", opts.tz);
  const query = params.toString();
  return apiFetch<FrontingAnalytics>(
    `/v1/analytics/fronting${query ? `?${query}` : ""}`,
  );
}
