"use client";

import { useEffect, useRef, type MutableRefObject, type RefObject } from "react";

/**
 * Tracks whether the reader is following the newest message in a scroll
 * container. Scrolling up away from the bottom stops following; scrolling
 * back within `threshold` px of the bottom resumes it. Callers pin to the
 * bottom only while `.current` is true, and set it to true themselves when
 * an action (sending, switching conversation) should bring the reader down.
 */
export function useFollowLatest(
  scrollRef: RefObject<HTMLElement | null>,
  threshold = 80,
): MutableRefObject<boolean> {
  const followRef = useRef(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let lastTop = el.scrollTop;
    const onScroll = () => {
      const top = el.scrollTop;
      const near = el.scrollHeight - top - el.clientHeight <= threshold;
      if (near) followRef.current = true;
      else if (top < lastTop - 1) followRef.current = false;
      lastTop = top;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [scrollRef, threshold]);

  return followRef;
}
