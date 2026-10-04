"use client";

import { useEffect, useRef, useState } from "react";

import { mediaRef } from "@/lib/chat/media-ref";
import type { ChatMediaUrlResponse } from "@/types";

/**
 * One inline media attachment from the **private** media bucket (PR-5).
 *
 * The transcript only carries the object path, so this component asks the BFF
 * (`GET /api/chat/media?path=…`) for a short-lived signed URL once it is near
 * the viewport (400px margin), so a long history doesn't sign every image.
 * The server re-checks that the path belongs to the requesting principal before
 * signing, so a tampered path simply renders as unavailable.
 */
/** Nearest scrolling ancestor, so the margin applies to the chat thread's box. */
function scrollParent(el: Element): Element | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const overflow = getComputedStyle(p).overflowY;
    if (overflow === "auto" || overflow === "scroll") return p;
  }
  return null;
}

export function ChatMedia({ path, alt }: { path: string; alt: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  // No IntersectionObserver (old browsers, SSR) → load straight away.
  const [near, setNear] = useState(
    () => typeof IntersectionObserver === "undefined",
  );
  // Keyed by path so a changed path resets to "loading" during render rather
  // than via a setState in the effect body.
  const [resolved, setResolved] = useState<{
    path: string;
    url: string | null;
    failed: boolean;
  }>({ path, url: null, failed: false });
  const { url, failed } =
    resolved.path === path ? resolved : { url: null, failed: false };

  useEffect(() => {
    const el = ref.current;
    if (near || !el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setNear(true);
          io.disconnect();
        }
      },
      { root: scrollParent(el), rootMargin: "400px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [near]);

  useEffect(() => {
    if (!near) return;
    let active = true;
    (async () => {
      try {
        const res = await fetch(mediaRef(path), { cache: "no-store" });
        const body = (await res.json()) as Partial<ChatMediaUrlResponse>;
        if (!active) return;
        setResolved({
          path,
          url: res.ok && body.url ? body.url : null,
          failed: !res.ok || !body.url,
        });
      } catch {
        if (active) setResolved({ path, url: null, failed: true });
      }
    })();
    return () => {
      active = false;
    };
  }, [path, near]);

  return (
    <span ref={ref} data-component="ChatMedia" className="mt-1 block">
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
