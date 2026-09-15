"use client";

import { useEffect } from "react";

import {
  markAppHealthy,
  reloadOnceForChunkError,
} from "@/lib/chunk-reload";

/**
 * Auto-recovers from a stale-bundle chunk-load failure (see
 * `lib/chunk-reload.ts`) for the errors that happen *outside* React's
 * render — a background route prefetch's dynamic import, for instance —
 * which never reach `global-error.tsx`'s boundary. Mounted once at the
 * root, alongside `ServiceWorkerRegister`.
 */
export function ChunkErrorListener() {
  useEffect(() => {
    // Reaching this effect at all means the current bundle just rendered
    // successfully — clear any earlier reload flag so a *future* deploy's
    // stale chunk gets its own single retry instead of being silently
    // blocked by an old one.
    markAppHealthy();

    const onError = (event: ErrorEvent) => {
      if (reloadOnceForChunkError(event.error ?? event.message)) {
        event.preventDefault();
      }
    };
    const onRejection = (event: PromiseRejectionEvent) => {
      if (reloadOnceForChunkError(event.reason)) {
        event.preventDefault();
      }
    };
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);
  return null;
}
