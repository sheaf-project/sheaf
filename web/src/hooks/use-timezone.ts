import { useCallback, useSyncExternalStore } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getAccessToken } from "@/lib/api-client";
import { getMySystem, updateMySystem } from "@/lib/systems";

/**
 * Display-timezone preference. Two tiers, mirroring the theme system's
 * "synced value + per-device override" shape but with the synced tier on the
 * real, exported `System.timezone` column rather than the instance-local
 * client-settings blob:
 *
 *   - Account default (`System.timezone`): syncs across the account's devices.
 *     null = "automatic" (each device uses its own local clock). Set from any
 *     device via PATCH /v1/systems/me.
 *   - Device override (localStorage `sheaf_timezone`): shadows the account
 *     value on this browser only and never writes back. Values: absent (follow
 *     the account), "auto" (pin this device to its own clock even if the
 *     account default is a fixed zone), or an IANA zone name.
 *
 * Resolution: device override > account default > browser-local. The resolved
 * value is what `useDateFormatters` renders in; `undefined` means "the
 * browser's own zone", which is exactly what "automatic" collapses to.
 */

const TZ_KEY = "sheaf_timezone";
// Device-override sentinel: "follow this device's own clock", distinct from
// "follow the account default" (which is the absence of any override).
const AUTO = "auto";

const listeners = new Set<() => void>();

function notify() {
  for (const cb of listeners) cb();
}

function getOverride(): string | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage.getItem(TZ_KEY);
}

function writeOverride(v: string | null) {
  if (v === null) localStorage.removeItem(TZ_KEY);
  else localStorage.setItem(TZ_KEY, v);
  notify();
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/** The browser/OS local IANA zone, e.g. "America/New_York". Falls back to
 *  "UTC" where `Intl` can't resolve one. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export interface UseTimezoneResult {
  /** IANA zone to render timestamps in, or `undefined` = the browser's local
   *  clock. Feed straight into the date formatters. */
  resolvedTimeZone: string | undefined;
  /** The synced account default (`System.timezone`). null = automatic. */
  accountTimeZone: string | null;
  /** This device's local override: null = follow the account, "auto" = this
   *  device's own clock, or a pinned IANA zone. */
  deviceOverride: string | null;
  /** True when this device follows the account default (no local override). */
  synced: boolean;
  /** Set the account-wide default (syncs to every device). */
  setAccountTimeZone: (tz: string | null) => Promise<void>;
  /** Set (or clear, with null) this device's local override. */
  setDeviceOverride: (v: string | null) => void;
}

export function useTimezone(): UseTimezoneResult {
  const queryClient = useQueryClient();
  const deviceOverride = useSyncExternalStore(
    subscribe,
    getOverride,
    () => null,
  );

  const { data: system } = useQuery({
    queryKey: ["system", "me"],
    queryFn: getMySystem,
    staleTime: 5 * 60 * 1000,
    enabled: getAccessToken() !== null,
  });
  const accountTimeZone = system?.timezone ?? null;

  // Resolution: device override > account default > browser-local.
  let resolvedTimeZone: string | undefined;
  if (deviceOverride === AUTO) {
    resolvedTimeZone = undefined;
  } else if (deviceOverride) {
    resolvedTimeZone = deviceOverride;
  } else {
    resolvedTimeZone = accountTimeZone ?? undefined;
  }

  const synced = deviceOverride === null;

  const setAccountTimeZone = useCallback(
    async (tz: string | null) => {
      await updateMySystem({ timezone: tz });
      queryClient.invalidateQueries({ queryKey: ["system", "me"] });
    },
    [queryClient],
  );

  const setDeviceOverride = useCallback((v: string | null) => {
    writeOverride(v);
  }, []);

  return {
    resolvedTimeZone,
    accountTimeZone,
    deviceOverride,
    synced,
    setAccountTimeZone,
    setDeviceOverride,
  };
}
