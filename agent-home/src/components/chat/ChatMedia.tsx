"use client";

import { useEffect, useState } from "react";

import { mediaRef } from "@/lib/chat/media-ref";
import type { ChatMediaUrlResponse } from "@/types";

/**
 * One inline media attachment from the **private** media bucket (PR-5).
 *
 * The transcript only carries the object path, so this component asks the BFF
 * (`GET /api/chat/media?path=…`) for a short-lived signed URL when it mounts.
 * The server re-checks that the path belongs to the requesting principal before
 * signing, so a tampered path simply renders as unavailable.
 */
export function ChatMedia({ path, alt }: { path: string; alt: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    setUrl(null);
    setFailed(false);
    (async () => {
      try {
        const res = await fetch(mediaRef(path), { cache: "no-store" });
        const body = (await res.json()) as Partial<ChatMediaUrlResponse>;
        if (!active) return;
        if (!res.ok || !body.url) {
          setFailed(true);
          return;
        }
        setUrl(body.url);
      } catch {
        if (active) setFailed(true);
      }
    })();
    return () => {
      active = false;
    };
  }, [path]);

  return (
    <span data-component="ChatMedia" className="mt-1 block">
      {url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={url} alt={alt} className="max-h-64 rounded-lg" />
      ) : (
        <span className="text-xs text-[var(--color-muted)]">
          {failed ? `${alt} — unavailable` : `Loading ${alt}…`}
        </span>
      )}
    </span>
  );
}
