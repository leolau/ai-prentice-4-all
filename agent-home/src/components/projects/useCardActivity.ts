"use client";

import { useEffect, useRef, useState } from "react";

import type { ToolChip } from "@/components/chat/LiveActivity";
import { readSseFrames, type StreamFrame } from "@/lib/chat/stream";

export interface CardActivity {
  /** The worker's reasoning so far (newest last), trimmed to a short tail. */
  reasoning: string;
  /** One chip per tool call, `done` once its completion arrives. */
  tools: ToolChip[];
  /** No worker is publishing for this card — said, not shown as silence. */
  unavailable: boolean;
  /** The worker's attempt is over; nothing more will arrive. */
  ended: boolean;
  /** Epoch seconds of the newest event, for "last update N s ago". */
  lastAt: number | null;
  /** The newest sequence number seen — the resume cursor. */
  cursor: number;
}

export const EMPTY_CARD_ACTIVITY: CardActivity = {
  reasoning: "",
  tools: [],
  unavailable: false,
  ended: false,
  lastAt: null,
  cursor: 0,
};

/** Reasoning kept in memory per row; the bar shows only the last lines. */
export const MAX_REASONING_CHARS = 4_000;
/** Tool chips kept per row — the newest win. */
export const MAX_TOOLS = 8;
/** How long to wait before asking again when no worker is publishing yet. */
export const UNAVAILABLE_RETRY_MS = 15_000;
/** How long to wait before reconnecting after a dropped stream. */
export const RECONNECT_MS = 3_000;

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Fold one `/cards/:id/activity` frame into the row's view. Same contract
 * as `applyActivityFrame` for a run — reasoning concatenates, a completion
 * marks the chip it opened — plus the cursor, the newest event's time, and
 * `end`. A real event after `unavailable` clears it (the worker started
 * publishing since the last look).
 */
export function applyCardActivityFrame(
  state: CardActivity,
  frame: StreamFrame,
): CardActivity {
  const { event, data } = frame;
  if (event === "unavailable") return { ...state, unavailable: true };
  if (event === "end") return { ...state, ended: true };
  const seq = typeof data.seq === "number" ? data.seq : null;
  const at = typeof data.at === "number" ? data.at : null;
  const base: CardActivity = {
    ...state,
    unavailable: false,
    cursor: seq != null && seq > state.cursor ? seq : state.cursor,
    lastAt: at != null ? Math.max(at, state.lastAt ?? 0) : state.lastAt,
  };
  if (event === "reasoning" || event === "status") {
    const text = str(data.text);
    if (!text) return base;
    const joined = state.reasoning ? `${state.reasoning}\n${text}` : text;
    return {
      ...base,
      reasoning:
        joined.length > MAX_REASONING_CHARS
          ? joined.slice(joined.length - MAX_REASONING_CHARS)
          : joined,
    };
  }
  if (event === "tool.start") {
    const tools = [
      ...state.tools,
      { id: str(data.tool_id), name: str(data.name) || "tool", done: false },
    ];
    return { ...base, tools: tools.slice(-MAX_TOOLS) };
  }
  if (event === "tool.complete" || event === "tool.end") {
    const id = str(data.tool_id);
    let matched = false;
    const tools = state.tools.map((t) => {
      if (!matched && !t.done && t.id === id) {
        matched = true;
        return { ...t, done: true };
      }
      return t;
    });
    if (!matched) tools.push({ id, name: str(data.name) || "tool", done: true });
    return { ...base, tools: tools.slice(-MAX_TOOLS) };
  }
  return state;
}

/**
 * Follow a board-dispatched card's live reasoning while it runs.
 *
 * Resumes from the last cursor on reconnect, so a dropped connection never
 * replays or loses lines. When no worker is publishing yet (`unavailable`,
 * e.g. the worker is still starting) it asks again every
 * `UNAVAILABLE_RETRY_MS` while the card is live; it stops on `end`.
 */
export function useCardActivity(
  slug: string,
  taskId: string,
  live: boolean,
  fetchImpl: typeof fetch = fetch,
): CardActivity {
  const [activity, setActivity] = useState<CardActivity>(EMPTY_CARD_ACTIVITY);
  const stateRef = useRef<CardActivity>(EMPTY_CARD_ACTIVITY);

  useEffect(() => {
    if (!live) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const schedule = (ms: number) => {
      if (!cancelled) timer = setTimeout(() => void connect(), ms);
    };
    const connect = async () => {
      let unavailable = false;
      let ended = false;
      try {
        const res = await fetchImpl(
          `/api/projects/${encodeURIComponent(slug)}/cards/${encodeURIComponent(taskId)}/activity?after=${stateRef.current.cursor}`,
          { signal: controller.signal },
        );
        if (!res.ok || !res.body) {
          // 404: the card is gone or hidden — nothing to retry for.
          if (res.status !== 404) schedule(RECONNECT_MS);
          return;
        }
        await readSseFrames(res, (frame) => {
          if (cancelled) return;
          if (frame.event === "unavailable") unavailable = true;
          if (frame.event === "end") ended = true;
          stateRef.current = applyCardActivityFrame(stateRef.current, frame);
          setActivity(stateRef.current);
        });
      } catch {
        // A dropped stream is retried below; the row keeps what it has.
      }
      if (cancelled || ended) return;
      schedule(unavailable ? UNAVAILABLE_RETRY_MS : RECONNECT_MS);
    };
    void connect();
    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [slug, taskId, live, fetchImpl]);

  return activity;
}
