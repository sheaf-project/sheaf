import { useCallback, useSyncExternalStore } from "react";

function makeLocalStorageHook(key: string, defaultValue: boolean) {
  const listeners = new Set<() => void>();

  function subscribe(cb: () => void) {
    listeners.add(cb);
    return () => listeners.delete(cb);
  }

  function getSnapshot(): boolean {
    const raw = localStorage.getItem(key);
    if (raw === null) return defaultValue;
    return raw === "true";
  }

  function set(value: boolean) {
    localStorage.setItem(key, String(value));
    for (const cb of listeners) cb();
  }

  return function usePreference(): [boolean, (v: boolean) => void] {
    const value = useSyncExternalStore(subscribe, getSnapshot, () => defaultValue);
    const setValue = useCallback((v: boolean) => set(v), []);
    return [value, setValue];
  };
}

export const useShowImageBadges = makeLocalStorageHook("sheaf:show-image-badges", true);
