import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api-client";

/** The stored settings blob for one client id (`web`, `android`, ...).
 *  A fresh account has no blob yet and the server 404s by design, which
 *  reads as an empty object here rather than an error. */
export function useClientSettings(clientId: string) {
  return useQuery({
    queryKey: ["client-settings", clientId],
    queryFn: async () => {
      try {
        const res = await apiFetch<{ settings: Record<string, unknown> }>(
          `/v1/settings/client/${encodeURIComponent(clientId)}`,
          { skipErrorToast: true },
        );
        return res.settings;
      } catch {
        return {};
      }
    },
    staleTime: 5 * 60 * 1000,
  });
}
