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
      // Cache-bust the SW script URL: a reverse proxy in front of the box
      // caches /sw.js by path and ignores max-age=0, so a new deploy's SW
      // never reaches the browser until the proxy cache expires. A query
      // parameter makes it a new URL the proxy hasn't seen. Bump this when
      // sw.js changes (the SW's own VERSION constant is the source of truth).
      navigator.serviceWorker.register("/sw.js?v=3").catch(() => {
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
