"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * A `localStorage`-backed value exposed as React state. Uses
 * `useSyncExternalStore` (not a `setState`-in-effect) so it hydrates cleanly
 * and re-renders on same-tab writes (via a custom event) and cross-tab writes
 * (via the native `storage` event). `parse`/`serialize` map the string form to
 * the value type; `fallback` is used on the server and when nothing is stored.
 */
export function usePersistentState<T>(
  key: string,
  fallback: T,
  parse: (raw: string) => T,
  serialize: (value: T) => string,
): [T, (next: T) => void] {
  const eventName = `persistent-state:${key}`;

  const subscribe = useCallback(
    (onChange: () => void) => {
      const onStorage = (e: StorageEvent) => {
        if (e.key === key) onChange();
      };
      window.addEventListener("storage", onStorage);
      window.addEventListener(eventName, onChange);
      return () => {
        window.removeEventListener("storage", onStorage);
        window.removeEventListener(eventName, onChange);
      };
    },
    [key, eventName],
  );

  const getSnapshot = useCallback((): T => {
    try {
      const raw = window.localStorage.getItem(key);
      return raw === null ? fallback : parse(raw);
    } catch {
      return fallback;
    }
  }, [key, fallback, parse]);

  const value = useSyncExternalStore(subscribe, getSnapshot, () => fallback);

  const setValue = useCallback(
    (next: T) => {
      try {
        window.localStorage.setItem(key, serialize(next));
      } catch {
        // A blocked localStorage still notifies listeners for this page.
      }
      window.dispatchEvent(new Event(eventName));
    },
    [key, eventName, serialize],
  );

  return [value, setValue];
}
