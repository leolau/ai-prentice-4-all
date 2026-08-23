"use client";

import { useCallback, useRef, useSyncExternalStore } from "react";

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
  // `useSyncExternalStore` re-checks the snapshot on every render and treats
  // any new reference as a change — a `parse` that builds an object would
  // therefore loop forever. Cache by raw string so identical stored content
  // keeps returning the SAME value.
  const cacheRef = useRef<{ raw: string | null; value: T } | null>(null);

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
    let raw: string | null;
    try {
      raw = window.localStorage.getItem(key);
    } catch {
      return fallback;
    }
    const cache = cacheRef.current;
    if (cache && cache.raw === raw) return cache.value;
    let value: T;
    try {
      value = raw === null ? fallback : parse(raw);
    } catch {
      value = fallback;
    }
    cacheRef.current = { raw, value };
    return value;
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
