import { useCallback, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, getAccessToken } from "@/lib/api-client";
import { patchWebSettings } from "@/lib/client-settings";
import { setShowTechnicalErrorsSnapshot } from "@/lib/developer-prefs-snapshot";

/**
 * Developer / power-user preferences. Backend-stored under
 * `client_settings/web.developer`. Today there's just one:
 *
 *   - `showTechnicalErrors`: when true, error toasts show the raw
 *     status code and backend detail. When false (default), toasts
 *     show a friendly summary derived from the status class.
 *
 * The api-client module isn't React and toasts errors synchronously,
 * so the resolved value is mirrored into the cycle-free snapshot
 * module (`developer-prefs-snapshot`). The snapshot is updated whenever
 * the backend value loads, and on user toggles.
 */

const WEB_SETTINGS_QUERY = ["client-settings", "web"] as const;

interface DeveloperBlob {
  show_technical_errors?: boolean;
}

interface WebSettingsShape {
  developer?: DeveloperBlob;
  [key: string]: unknown;
}

async function fetchWebSettings(): Promise<WebSettingsShape> {
  try {
    const res = await apiFetch<{ settings: WebSettingsShape }>(
      "/v1/settings/client/web",
      // No settings blob on a fresh account is the expected 404 here, not an
      // error the user should see toasted; we default to {} below.
      { skipErrorToast: true },
    );
    return res.settings ?? {};
  } catch {
    return {};
  }
}

export interface UseDeveloperPrefsResult {
  showTechnicalErrors: boolean;
  setShowTechnicalErrors: (next: boolean) => void;
}

export function useDeveloperPrefs(): UseDeveloperPrefsResult {
  const queryClient = useQueryClient();

  const { data: backend } = useQuery({
    queryKey: WEB_SETTINGS_QUERY,
    queryFn: fetchWebSettings,
    staleTime: 5 * 60 * 1000,
    enabled: getAccessToken() !== null,
    retry: false,
  });

  const showTechnicalErrors =
    backend?.developer?.show_technical_errors ?? false;

  // Keep the module-level snapshot in lockstep with whatever React is
  // rendering. Without this, the api-client would read a stale value
  // until the next page reload.
  useEffect(() => {
    setShowTechnicalErrorsSnapshot(showTechnicalErrors);
  }, [showTechnicalErrors]);

  const setShowTechnicalErrors = useCallback(
    (next: boolean) => {
      // Optimistic snapshot update so the very next error toast picks
      // up the new value even if the network round-trip is slow.
      setShowTechnicalErrorsSnapshot(next);
      void patchWebSettings({
        developer: { show_technical_errors: next },
      }).then(() => {
        queryClient.invalidateQueries({ queryKey: WEB_SETTINGS_QUERY });
      });
    },
    [queryClient],
  );

  return { showTechnicalErrors, setShowTechnicalErrors };
}
