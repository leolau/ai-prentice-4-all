"use client";

import { useEffect } from "react";

/**
 * Registers the PWA service worker (`/sw.js`) for the installable app + offline
 * shell. No-op during SSR and when the browser lacks service-worker support.
 */
export function ServiceWorkerRegister() {
  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
      return;
    }
    const register = () => {
      // Register via /api/sw (a dynamic route) instead of /sw.js (a static
      // file): a reverse proxy in front of the box caches static files by
      // path and ignores cache-control headers, so a static SW gets stuck
      // at an old version after every deploy. The API route is never cached
      // and sets Service-Worker-Allowed: / so the SW can control "/".
      navigator.serviceWorker
        .register("/api/sw", { scope: "/" })
        .catch(() => {
          // Registration failures are non-fatal — the app works without the SW.
        });
    };
    if (document.readyState === "complete") {
      register();
    } else {
      window.addEventListener("load", register, { once: true });
    }
  }, []);
  return null;
}
