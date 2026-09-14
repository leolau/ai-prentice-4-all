"use client";

import { useEffect, useRef } from "react";

import type { ProjectCardDetail } from "@/types";

/**
 * A running card's re-read interval — matches the run page's
 * `RUN_POLL_INTERVAL_MS`. This is the surface a person watches while a
 * board-dispatched worker (its own process, no reasoning stream) is
 * actually doing something; the page must not sit frozen the whole time.
 */
export const CARD_POLL_INTERVAL_MS = 5_000;

/** Only `running` has anything to poll for: a worker is on it right now,
 * and its heartbeat note / last comment can change between reads. Every
 * other status is either not-yet-started or already closed — a reload
 * (or the run page's own live view) is how those move. */
export function isCardLive(status: string): boolean {
  return status === "running";
}

/**
 * One re-read of `GET /api/projects/:slug/cards/:taskId`, split out of the
 * hook so the contract is testable without a DOM — mirrors
 * `createRunPoller` (`useRunLive.ts`) exactly.
 */
export function createCardPoller(
  slug: string,
  taskId: string,
  onCard: (card: ProjectCardDetail) => void,
  fetchImpl: typeof fetch = fetch,
): { tick: () => Promise<boolean> } {
  const tick = async (): Promise<boolean> => {
    if (
      typeof document !== "undefined" &&
      document.visibilityState !== "visible"
    ) {
      return true; // hidden is not finished — keep the timer armed
    }
    try {
      const res = await fetchImpl(
        `/api/projects/${encodeURIComponent(slug)}/cards/${encodeURIComponent(taskId)}`,
      );
      if (!res.ok) return true;
      const data = (await res.json().catch(() => null)) as ProjectCardDetail | null;
      if (data == null || typeof data.status !== "string") return true;
      onCard(data);
      return isCardLive(data.status);
    } catch {
      return true;
    }
  };
  return { tick };
}

/**
 * Keep one card row current while a worker is actually on it. Polling
 * stops the moment the card leaves `running` and does not restart on its
 * own — reopening the page (or the status changing again) re-evaluates.
 */
export function useCardLive(
  slug: string,
  taskId: string,
  status: string,
  onCard: (card: ProjectCardDetail) => void,
): void {
  const onCardRef = useRef(onCard);
  useEffect(() => {
    onCardRef.current = onCard;
  }, [onCard]);

  useEffect(() => {
    if (!isCardLive(status)) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poller = createCardPoller(slug, taskId, (card) =>
      onCardRef.current(card),
    );
    const loop = async () => {
      const live = await poller.tick();
      if (stopped || !live) return;
      timer = setTimeout(() => void loop(), CARD_POLL_INTERVAL_MS);
    };
    timer = setTimeout(() => void loop(), CARD_POLL_INTERVAL_MS);
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [slug, taskId, status]);
}
