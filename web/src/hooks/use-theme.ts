import { useCallback, useSyncExternalStore } from "react";

type Theme = "dark" | "light";

const themeListeners = new Set<() => void>();

function getTheme(): Theme {
  return (localStorage.getItem("sheaf_theme") as Theme) || "dark";
}

function subscribeTheme(cb: () => void) {
  themeListeners.add(cb);
  return () => themeListeners.delete(cb);
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.style.colorScheme = theme;
  localStorage.setItem("sheaf_theme", theme);
  themeListeners.forEach((cb) => cb());
}

export function useTheme() {
  const theme = useSyncExternalStore(subscribeTheme, getTheme);

  const toggleTheme = useCallback(() => {
    applyTheme(theme === "dark" ? "light" : "dark");
  }, [theme]);

  return { theme, toggleTheme };
}

// ---------------------------------------------------------------------------
// UI Scale
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
  return () => scaleListeners.delete(cb);
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
