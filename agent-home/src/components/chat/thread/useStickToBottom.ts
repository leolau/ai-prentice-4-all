"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

export interface StickToBottom {
  /** The reader is within `threshold` px of the bottom (or following it). */
  atBottom: boolean;
  /** Content grew while the reader was scrolled up. */
  hasNew: boolean;
  scrollToBottom(smooth?: boolean): void;
  /** Call right before older messages are prepended, to keep the viewport put. */
  preservePrepend(): void;
}

const frame = (cb: () => void): (() => void) => {
  if (typeof requestAnimationFrame === "function") {
    const id = requestAnimationFrame(cb);
    return () => cancelAnimationFrame(id);
  }
  const id = setTimeout(cb, 16);
  return () => clearTimeout(id);
};

/**
 * Follow the bottom of a scroll container only while the reader is already
 * there. Content growth (observed on `contentRef`) pins the view when
 * following, otherwise raises `hasNew`. Leaving the bottom requires the reader
 * to scroll *up* — growth alone never unsticks, so a fast stream can't race
 * the scroll listener. Both elements must be mounted with the calling component.
 */
export function useStickToBottom(
  scrollRef: RefObject<HTMLElement | null>,
  contentRef: RefObject<HTMLElement | null>,
  opts?: { threshold?: number },
): StickToBottom {
  const threshold = opts?.threshold ?? 80;
  const [atBottom, setAtBottomState] = useState(true);
  const [hasNew, setHasNewState] = useState(false);
  const atBottomRef = useRef(true);
  const hasNewRef = useRef(false);
  const thresholdRef = useRef(threshold);
  const prependRef = useRef<number | null>(null);
  const prependTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastTopRef = useRef(0);
  const lastHeightRef = useRef(0);
  // Distance from the content end to the viewport top; grows only when
  // content is added below the reader (growth above is scroll-anchored).
  const lastDistRef = useRef(0);

  useEffect(() => {
    thresholdRef.current = threshold;
  }, [threshold]);

  const setHasNew = useCallback((v: boolean) => {
    if (hasNewRef.current === v) return;
    hasNewRef.current = v;
    setHasNewState(v);
  }, []);

  const setAtBottom = useCallback(
    (v: boolean) => {
      if (v) setHasNew(false);
      if (atBottomRef.current === v) return;
      atBottomRef.current = v;
      setAtBottomState(v);
    },
    [setHasNew],
  );

  const pin = useCallback((el: HTMLElement) => {
    el.scrollTop = el.scrollHeight;
    lastTopRef.current = el.scrollTop;
    lastDistRef.current = el.scrollHeight - el.scrollTop;
  }, []);

  // Initial mount: start at the newest message.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    pin(el);
    lastHeightRef.current = el.scrollHeight;
  }, [scrollRef, pin]);

  // Passive, rAF-throttled scroll tracking.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let pending = false;
    let cancel: (() => void) | null = null;
    const measure = () => {
      pending = false;
      cancel = null;
      const top = el.scrollTop;
      const near =
        el.scrollHeight - top - el.clientHeight <= thresholdRef.current;
      const movedUp = top < lastTopRef.current - 1;
      lastTopRef.current = top;
      // Against the last *observed* height, so growth not yet seen by the
      // ResizeObserver still counts as new.
      lastDistRef.current = lastHeightRef.current - top;
      if (near) setAtBottom(true);
      else if (movedUp) setAtBottom(false);
    };
    const onScroll = () => {
      if (pending) return;
      pending = true;
      cancel = frame(measure);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      el.removeEventListener("scroll", onScroll);
      cancel?.();
    };
  }, [scrollRef, setAtBottom]);

  // React to content growth / viewport resizes.
  useEffect(() => {
    const el = scrollRef.current;
    const content = contentRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const height = el.scrollHeight;
      const grew = height > lastHeightRef.current;
      lastHeightRef.current = height;
      const recorded = prependRef.current;
      if (recorded !== null && grew) {
        prependRef.current = null;
        el.scrollTop = height - recorded;
        lastTopRef.current = el.scrollTop;
        lastDistRef.current = height - el.scrollTop;
        return;
      }
      if (atBottomRef.current) {
        pin(el);
        return;
      }
      const dist = height - el.scrollTop;
      if (dist > lastDistRef.current + 1) setHasNew(true);
      lastDistRef.current = dist;
    });
    ro.observe(el);
    if (content) ro.observe(content);
    return () => ro.disconnect();
  }, [scrollRef, contentRef, pin, setHasNew]);

  const scrollToBottom = useCallback(
    (smooth = false) => {
      const el = scrollRef.current;
      if (!el) return;
      setAtBottom(true);
      if (smooth && typeof el.scrollTo === "function") {
        el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
      } else {
        pin(el);
      }
    },
    [scrollRef, setAtBottom, pin],
  );

  const preservePrepend = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    prependRef.current = el.scrollHeight - el.scrollTop;
    lastHeightRef.current = el.scrollHeight;
    // An empty page never grows the content; don't leave the restore armed.
    if (prependTimerRef.current) clearTimeout(prependTimerRef.current);
    prependTimerRef.current = setTimeout(() => {
      prependRef.current = null;
      prependTimerRef.current = null;
    }, 1000);
  }, [scrollRef]);

  useEffect(
    () => () => {
      if (prependTimerRef.current) clearTimeout(prependTimerRef.current);
    },
    [],
  );

  return { atBottom, hasNew, scrollToBottom, preservePrepend };
}
