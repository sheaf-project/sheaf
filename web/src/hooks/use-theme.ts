import { useCallback, useEffect, useSyncExternalStore } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, getAccessToken } from "@/lib/api-client";
import { patchWebSettings } from "@/lib/client-settings";
import {
  DEFAULT_PALETTE,
  paletteFromId,
  type PaletteId,
} from "@/lib/palettes";

/**
 * Theme system. Two user preferences:
 *
 *   - `mode`: "system" | "light" | "dark" — when "system" the
 *     effective light/dark follows the OS via `prefers-color-scheme`.
 *   - `palette`: one of the shipped palette ids — selects which colour
 *     catalog (see `index.css`) gets applied via `data-palette` on
 *     `<html>`.
 *
 * Plus a `synced` toggle that decides storage tier:
 *
 *   - on: values live in `client_settings/web.theme` and follow the
 *     account across browsers.
 *   - off: values live in localStorage on this browser only. The
 *     backend value (if any) stays untouched and represents what
 *     other browsers logged into this account will use.
 *
 * Resolution order on load: localStorage > backend > app defaults.
 * Whichever tier the active values came from determines the displayed
 * `synced` state — flipping the picker doesn't surprise the user
 * about where their choice ends up.
 *
 * Pre-paint script (`index.html`) reads localStorage synchronously
 * and sets the `data-palette` attribute + `.dark` class before React
 * mounts, so the first paint never flashes the wrong palette. The
 * backend value is fetched after mount; if localStorage is empty and
 * the backend has a different value, a one-time visual switch happens
 * after the initial paint. Acceptable for the rare case (fresh browser
 * on an already-synced account).
 */

// ---------------------------------------------------------------------------
// Mode (light / dark / system)
// ---------------------------------------------------------------------------

export type ThemeMode = "system" | "light" | "dark";
const MODE_KEY = "sheaf_theme";
const PALETTE_KEY = "sheaf_palette";

// Lazy media query so we don't construct it during SSR / tests where
// `matchMedia` is missing. Memoised after first call.
let _prefersDark: MediaQueryList | null = null;
function prefersDarkMQ(): MediaQueryList | null {
  if (typeof window === "undefined" || !window.matchMedia) return null;
  if (_prefersDark === null) {
    _prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
  }
  return _prefersDark;
}

function resolveEffective(mode: ThemeMode): "light" | "dark" {
  if (mode === "dark") return "dark";
  if (mode === "light") return "light";
  return prefersDarkMQ()?.matches ? "dark" : "light";
}

function applyMode(effective: "light" | "dark"): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", effective === "dark");
  document.documentElement.style.colorScheme = effective;
}

function applyPalette(palette: PaletteId): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-palette", palette);
}

// ---------------------------------------------------------------------------
// localStorage stores with useSyncExternalStore subscriptions
// ---------------------------------------------------------------------------

const modeListeners = new Set<() => void>();
const paletteListeners = new Set<() => void>();

function notify(set: Set<() => void>) {
  for (const cb of set) cb();
}

function getStoredMode(): ThemeMode | null {
  if (typeof localStorage === "undefined") return null;
  const v = localStorage.getItem(MODE_KEY);
  if (v === "dark" || v === "light" || v === "system") return v;
  return null;
}

function getStoredPalette(): PaletteId | null {
  if (typeof localStorage === "undefined") return null;
  const v = localStorage.getItem(PALETTE_KEY);
  return v ? paletteFromId(v) : null;
}

function writeLocalMode(mode: ThemeMode) {
  localStorage.setItem(MODE_KEY, mode);
  notify(modeListeners);
}

function writeLocalPalette(palette: PaletteId) {
  localStorage.setItem(PALETTE_KEY, palette);
  notify(paletteListeners);
}

function clearLocalMode() {
  localStorage.removeItem(MODE_KEY);
  notify(modeListeners);
}

function clearLocalPalette() {
  localStorage.removeItem(PALETTE_KEY);
  notify(paletteListeners);
}

function subscribeMode(cb: () => void): () => void {
  modeListeners.add(cb);
  return () => {
    modeListeners.delete(cb);
  };
}

function subscribePalette(cb: () => void): () => void {
  paletteListeners.add(cb);
  return () => {
    paletteListeners.delete(cb);
  };
}

// ---------------------------------------------------------------------------
// Backend (client_settings/web.theme)
// ---------------------------------------------------------------------------

interface BackendTheme {
  mode?: ThemeMode;
  palette?: string;
}

interface WebClientSettings {
  theme?: BackendTheme;
  // The blob can hold other keys (dismissed announcements etc.); we
  // only read .theme here but keep the index signature loose so the
  // query result type aligns with what other callers see.
  [key: string]: unknown;
}

/** React-query key used for the web client settings record. Same key
 *  is invalidated by other features (announcement dismissal etc.) so
 *  a theme change here also propagates wherever else the blob is
 *  consumed. */
const WEB_SETTINGS_QUERY = ["client-settings", "web"] as const;

async function fetchWebSettings(): Promise<WebClientSettings> {
  try {
    const res = await apiFetch<{ settings: WebClientSettings }>(
      "/v1/settings/client/web",
      // A fresh account has no settings blob yet, so the backend 404s by
      // design. That's the normal first-run state here, not an error worth
      // a toast; we fall back to {} below.
      { skipErrorToast: true },
    );
    return res.settings ?? {};
  } catch {
    return {};
  }
}

// ---------------------------------------------------------------------------
// Public hook
// ---------------------------------------------------------------------------

export interface UseThemeResult {
  /** The user's preference value, NOT the resolved light/dark when in
   *  "system" mode. Use `effectiveMode` for the applied value. */
  mode: ThemeMode;
  /** Resolved light/dark — "system" reflects the OS preference, kept
   *  live as the user toggles their OS theme. */
  effectiveMode: "light" | "dark";
  palette: PaletteId;
  /** True when the active values came from the backend, i.e. this
   *  browser is following the account-synced preference. False when
   *  there's a localStorage override on this browser. */
  synced: boolean;
  setMode: (mode: ThemeMode) => void;
  setPalette: (palette: PaletteId) => void;
  /** Toggle whether this browser stays synced with the account or
   *  pins to a local override. Flipping ON copies current values to
   *  the backend and clears the local override. Flipping OFF writes
   *  current values to localStorage and leaves the backend alone. */
  setSynced: (synced: boolean) => void;
  /** Convenience for the existing dark-only call sites: cycle the
   *  effective light/dark. Never lands on "system" — users using the
   *  quick toggle have made an explicit choice; "system" lives in
   *  the Appearance settings. */
  toggleEffective: () => void;
}

export function useTheme(): UseThemeResult {
  const queryClient = useQueryClient();
  const storedMode = useSyncExternalStore(
    subscribeMode,
    getStoredMode,
    () => null,
  );
  const storedPalette = useSyncExternalStore(
    subscribePalette,
    getStoredPalette,
    () => null,
  );

  // Backend value. Only fetched when authenticated — login / forgot-
  // password pages don't have a session yet, so let them stay on
  // localStorage / defaults until after sign-in.
  const { data: backend } = useQuery({
    queryKey: WEB_SETTINGS_QUERY,
    queryFn: fetchWebSettings,
    staleTime: 5 * 60 * 1000,
    enabled: getAccessToken() !== null,
    retry: false,
  });

  const backendMode = backend?.theme?.mode;
  const backendPalette = backend?.theme?.palette
    ? paletteFromId(backend.theme.palette)
    : null;

  // Resolution: localStorage > backend > defaults. Default mode is
  // "dark" (matches what the app shipped with before this feature);
  // default palette is "classic" (also matches the existing look).
  const mode: ThemeMode = storedMode ?? backendMode ?? "dark";
  const palette: PaletteId =
    storedPalette ?? backendPalette ?? DEFAULT_PALETTE;
  // synced reflects which tier the active values came from. If the
  // user has any localStorage override, we report not-synced even
  // when the backend happens to hold the same value — so the
  // toggle's displayed state matches actual storage.
  const synced = storedMode === null && storedPalette === null;

  const effectiveMode = resolveEffective(mode);

  // Push the effective mode + palette into the DOM whenever they
  // change. The pre-paint script handles the very first paint; this
  // covers subsequent in-app changes and the backend-arrives-after-
  // mount case.
  useEffect(() => {
    applyMode(effectiveMode);
  }, [effectiveMode]);
  useEffect(() => {
    applyPalette(palette);
  }, [palette]);

  // Re-apply when the OS theme flips, but only while we're actually
  // listening (mode === "system"). Adds a listener for the duration
  // of system mode and removes it on switch.
  useEffect(() => {
    if (mode !== "system") return;
    const mq = prefersDarkMQ();
    if (!mq) return;
    const onChange = () => applyMode(resolveEffective("system"));
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [mode]);

  const setMode = useCallback(
    (next: ThemeMode) => {
      if (synced) {
        void patchWebSettings({ theme: { mode: next, palette } }).then(() => {
          queryClient.invalidateQueries({ queryKey: WEB_SETTINGS_QUERY });
        });
      } else {
        writeLocalMode(next);
      }
    },
    [synced, palette, queryClient],
  );

  const setPalette = useCallback(
    (next: PaletteId) => {
      if (synced) {
        void patchWebSettings({ theme: { mode, palette: next } }).then(() => {
          queryClient.invalidateQueries({ queryKey: WEB_SETTINGS_QUERY });
        });
      } else {
        writeLocalPalette(next);
      }
    },
    [synced, mode, queryClient],
  );

  const setSynced = useCallback(
    (next: boolean) => {
      if (next === synced) return;
      if (next) {
        // Going synced: promote current effective values to the
        // backend, then clear localStorage so subsequent reads come
        // from the backend tier.
        void patchWebSettings({ theme: { mode, palette } }).then(() => {
          clearLocalMode();
          clearLocalPalette();
          queryClient.invalidateQueries({ queryKey: WEB_SETTINGS_QUERY });
        });
      } else {
        // Going local: stash current values in localStorage so this
        // browser stays pinned regardless of what the backend says
        // now or later.
        writeLocalMode(mode);
        writeLocalPalette(palette);
      }
    },
    [synced, mode, palette, queryClient],
  );

  const toggleEffective = useCallback(() => {
    setMode(effectiveMode === "dark" ? "light" : "dark");
  }, [effectiveMode, setMode]);

  return {
    mode,
    effectiveMode,
    palette,
    synced,
    setMode,
    setPalette,
    setSynced,
    toggleEffective,
  };
}

// ---------------------------------------------------------------------------
// UI Scale (unchanged)
// ---------------------------------------------------------------------------

export type UiScale = 75 | 100 | 125 | 150;
const VALID_SCALES: UiScale[] = [75, 100, 125, 150];

const scaleListeners = new Set<() => void>();

function getScale(): UiScale {
  const stored = Number(localStorage.getItem("sheaf_ui_scale"));
  return VALID_SCALES.includes(stored as UiScale) ? (stored as UiScale) : 100;
}

function subscribeScale(cb: () => void) {
  scaleListeners.add(cb);
  return () => {
    scaleListeners.delete(cb);
  };
}

function applyScale(scale: UiScale) {
  document.documentElement.style.zoom = scale === 100 ? "" : `${scale}%`;
  localStorage.setItem("sheaf_ui_scale", String(scale));
  scaleListeners.forEach((cb) => cb());
}

// Apply on load
applyScale(getScale());

export function useUiScale() {
  const scale = useSyncExternalStore(subscribeScale, getScale);

  const setScale = useCallback((s: UiScale) => {
    applyScale(s);
  }, []);

  return { scale, setScale, scales: VALID_SCALES };
}
