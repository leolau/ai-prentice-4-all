"use client";

import { useEffect, useRef, useState } from "react";

import { readSseFrames, type StreamFrame } from "@/lib/chat/stream";
import type { ToolChip } from "@/components/chat/LiveActivity";

export interface RunActivity {
  /** The agent's reasoning so far, concatenated in arrival order. */
  reasoning: string;
  /** One chip per tool call, `done` once its completion arrives. */
  tools: ToolChip[];
  /**
   * The box has no live view of this run — its steps run in another process
   * (a board-dispatched card), or the run ended long enough ago that the
   * buffer was dropped. Said out loud rather than shown as an idle run.
   */
  unavailable: boolean;
}

const EMPTY: RunActivity = { reasoning: "", tools: [], unavailable: false };

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Fold one activity frame into the accumulated view.
 *
 * Split out of the hook so the contract is testable without a DOM: reasoning
 * concatenates, a tool's completion marks the chip it opened (matched on the
 * tool id) rather than appending a second chip, and an `unavailable` frame is
 * a state, not an error. A frame carrying a tool's arguments or results
 * cannot appear — the server never puts them on the wire — and nothing here
 * would render them if it did.
 */
export function applyActivityFrame(
  state: RunActivity,
  frame: StreamFrame,
): RunActivity {
  const { event, data } = frame;
  if (event === "unavailable") {
    return { ...state, unavailable: true };
  }
  if (event === "reasoning" || event === "status") {
    const text = str(data.text);
    if (!text) return state;
    return {
      ...state,
      reasoning: state.reasoning ? `${state.reasoning}\n${text}` : text,
    };
  }
  if (event === "tool.start") {
    return {
      ...state,
      tools: [
        ...state.tools,
        { id: str(data.tool_id), name: str(data.name) || "tool", done: false },
      ],
    };
  }
  if (event === "tool.complete") {
    const id = str(data.tool_id);
    let matched = false;
    const tools = state.tools.map((t) => {
      if (!matched && !t.done && t.id === id) {
        matched = true;
        return { ...t, done: true };
      }
      return t;
    });
    if (!matched) {
      tools.push({ id, name: str(data.name) || "tool", done: true });
    }
    return { ...state, tools };
  }
  return state;
}

/**
 * Follow a run's reasoning and tool activity while it is live.
 *
 * The stream replays from the beginning on connect, so opening the page
 * mid-run — or on a second device — shows the whole run rather than the
 * remainder. It stops on `end` and is torn down when the run reaches a
 * terminal status; a transport failure leaves whatever was already received
 * on screen instead of an error, because activity is a view of the work, not
 * the work itself.
 */
export function useRunActivity(
  slug: string,
  runNo: number,
  live: boolean,
): RunActivity {
  const [activity, setActivity] = useState<RunActivity>(EMPTY);
  const stateRef = useRef<RunActivity>(EMPTY);

  useEffect(() => {
    if (!live) return;
    let cancelled = false;
    const controller = new AbortController();
    stateRef.current = EMPTY;

    void (async () => {
      try {
        // Cleared here rather than in the effect body: a new run starts
        // from nothing, but resetting synchronously would re-render twice
        // for a hook whose whole job is to be cheap while a run is live.
        setActivity(EMPTY);
        const res = await fetch(
          `/api/projects/${encodeURIComponent(slug)}/runs/${runNo}/activity?after=0`,
          { signal: controller.signal },
        );
        if (!res.ok || !res.body) return;
        await readSseFrames(res, (frame) => {
          if (cancelled) return;
          stateRef.current = applyActivityFrame(stateRef.current, frame);
          setActivity(stateRef.current);
        });
      } catch {
        // A dropped stream is not something a person can act on; the run
        // page's own polling still reports the run's status truthfully.
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [slug, runNo, live]);

  return activity;
}
