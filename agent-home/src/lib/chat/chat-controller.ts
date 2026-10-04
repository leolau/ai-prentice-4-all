/**
 * The shared chat engine behind both chat surfaces (main `/chat` pane and the
 * floating lead chat). One controller owns, per conversation, the paged
 * visible transcript and at most one in-flight turn, and exposes them as an
 * external store for `useSyncExternalStore`:
 *
 * - Thread snapshots change only when the transcript, its paging, or its
 *   note/error change — never on a streamed token.
 * - Live-turn snapshots are rebuilt at most once per animation frame from the
 *   delta buffer, so N tokens in a frame cost one render of the live bubble.
 * - Turns run per session key (`keyOf`), so switching conversations never
 *   cancels or cross-contaminates an in-flight turn.
 *
 * Every read carries the profile (`withProfileQuery`), every write
 * (`withProfileBody`) — a profile is a whole HERMES_HOME.
 */
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import type { ChatActivity } from "@/components/chat/StatusIndicator";
import { withProfileBody, withProfileQuery } from "@/lib/chat/profile";
import {
  attachChatStream,
  cancelChatTurn,
  streamChatTurn,
  type ChatStreamHandlers,
} from "@/lib/chat/stream";
import { mergeOlderPage, reconcileTail, visibleTurns } from "@/lib/chat/transcript";
import {
  decisionText,
  deriveActivity,
  emptyActivity,
  STOPPED_NOTE,
  type TurnActivity,
} from "@/lib/chat/turn-activity";
import type {
  ChatApprovalRequest,
  ChatAttachment,
  ChatMessage,
  ChatMessagesResponse,
} from "@/types";

/** Turn key of a not-yet-created conversation. */
export const NEW_KEY = "__new__";
export const keyOf = (id: string | null): string => id ?? NEW_KEY;
export const TRANSCRIPT_PAGE_SIZE = 40;
/** Page size `loadAll` walks history with (the BFF's `limit` cap). */
const LOAD_ALL_PAGE_SIZE = 500;
/** Idle-watch cadence for turns started on other surfaces. */
export const ACTIVE_WATCH_MS = 10_000;
const CLIENT_KEY_PREFIX = "local:";
const UNCONFIRMED_RELOADS = 3;

export interface ThreadSnapshot {
  sessionId: string | null;
  /** Visible turns, oldest→newest; optimistic rows carry `clientKey`. */
  messages: ChatMessage[];
  /** Older pages exist on the server. */
  hasMore: boolean;
  status: "idle" | "loading" | "ready" | "error";
  loadingOlder: boolean;
  /** Load failure or last turn's error; cleared on next open/send. */
  error: string | null;
  /** Approval-decision / "stopped" note of the last turn. */
  note: string | null;
}

export interface LiveTurnSnapshot {
  key: string;
  /** Accumulated streamed reply. */
  assistantText: string;
  activity: TurnActivity;
  phase: ChatActivity;
  approval: ChatApprovalRequest | null;
  resolvingApproval: boolean;
}

export interface ChatControllerOptions {
  profile?: string;
  pageSize?: number;
  /**
   * Adopt the id a turn lands on (main chat: true). The lead chat passes
   * false: a compacted conversation answers under a continuation id, and
   * re-pinning to it would fork this browser's lead chat from every other.
   * A NEW_KEY turn always lands on its new id (there is no other id).
   */
  adoptLandedSession?: boolean;
  onTurnSettled?(key: string, landedSessionId: string | null): void;
}

interface ThreadState {
  snap: ThreadSnapshot;
  /** Last latest-page load issued / applied (stale responses are dropped). */
  loadSeq: number;
  appliedSeq: number;
  older: Promise<void> | null;
}

interface LiveState {
  key: string;
  text: string;
  /** `assistant.completed` content, when the server sent one. */
  final: string;
  activity: TurnActivity;
  approval: ChatApprovalRequest | null;
  resolving: boolean;
  abort: AbortController;
  /** The optimistic user row of this turn (null for an attached turn). */
  userClientKey: string | null;
  snap: LiveTurnSnapshot;
}

type Frame = { raf: number } | { timer: ReturnType<typeof setTimeout> };

const isAbort = (err: unknown) =>
  (err instanceof DOMException || err instanceof Error) && err.name === "AbortError";

const messageOf = (err: unknown, fallback: string) =>
  err instanceof Error && err.message ? err.message : fallback;

function emptyThread(key: string): ThreadSnapshot {
  return {
    sessionId: key === NEW_KEY ? null : key,
    messages: [],
    hasMore: false,
    status: "idle",
    loadingOlder: false,
    error: null,
    note: null,
  };
}

function liveSnapshot(live: LiveState): LiveTurnSnapshot {
  return {
    key: live.key,
    assistantText: live.text,
    activity: live.activity,
    phase: deriveActivity({
      busy: true,
      turn: live.activity,
      approval: live.approval !== null,
      hasOutput: live.text !== "",
    }),
    approval: live.approval,
    resolvingApproval: live.resolving,
  };
}

export class ChatController {
  private opts: ChatControllerOptions;
  private threads = new Map<string, ThreadState>();
  private lives = new Map<string, LiveState>();
  private busy: readonly string[] = [];
  private threadSubs = new Map<string, Set<() => void>>();
  private liveSubs = new Map<string, Set<() => void>>();
  private busySubs = new Set<() => void>();
  private dirtyThreads = new Set<string>();
  private dirtyLive = new Set<string>();
  private dirtyBusy = false;
  private frame: Frame | null = null;
  private optSeq = 0;
  /**
   * Optimistic rows of settled turns the server may not have written yet,
   * with how many more tail reloads may keep them before giving up.
   */
  private unconfirmed = new Map<string, number>();
  private attaching = new Set<string>();
  /** Last run streamed to completion per key — never re-attached to. */
  private finishedRuns = new Map<string, string>();
  private idleListeners = new Map<string, Set<() => void>>();
  private watchers = new Set<() => void>();
  private refs = 0;
  private pendingDispose: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;

  constructor(opts: ChatControllerOptions = {}) {
    this.opts = { ...opts };
  }

  /** Replace the options (latest props); takes effect on the next request. */
  setOptions(opts: ChatControllerOptions): void {
    this.opts = { ...opts };
  }

  // ── External store ────────────────────────────────────────────────

  subscribeThread(sessionId: string | null, cb: () => void): () => void {
    return addSub(this.threadSubs, keyOf(sessionId), cb);
  }

  getThread(sessionId: string | null): ThreadSnapshot {
    return this.thread(keyOf(sessionId)).snap;
  }

  subscribeLive(key: string, cb: () => void): () => void {
    return addSub(this.liveSubs, key, cb);
  }

  getLive(key: string): LiveTurnSnapshot | null {
    return this.lives.get(key)?.snap ?? null;
  }

  subscribeBusy(cb: () => void): () => void {
    this.busySubs.add(cb);
    return () => {
      this.busySubs.delete(cb);
    };
  }

  getBusyKeys(): readonly string[] {
    return this.busy;
  }

  // ── Transcript ────────────────────────────────────────────────────

  /** SSR first page; ignored once the thread has loaded from the client. */
  seed(sessionId: string, messages: ChatMessage[], hasMore: boolean): void {
    const t = this.thread(sessionId);
    if (t.appliedSeq > 0 || t.snap.status === "ready") return;
    const { messages: merged } = reconcileTail(t.snap.messages, visibleTurns(messages), {
      pageHasMore: hasMore,
      keep: (m) => !!m.clientKey,
    });
    this.patch(sessionId, { messages: merged, hasMore, status: "ready" });
  }

  /** Stale-while-revalidate: cached rows stay on screen while the first page loads. */
  async open(sessionId: string): Promise<void> {
    const t = this.thread(sessionId);
    this.patch(sessionId, {
      error: null,
      status: t.snap.status === "ready" ? "ready" : "loading",
    });
    await this.loadLatest(sessionId, false);
  }

  /** Quiet reconcile — no loading state, failures ignored. */
  async reload(sessionId: string): Promise<void> {
    await this.loadLatest(sessionId, true);
  }

  loadOlder(sessionId: string): Promise<void> {
    return this.loadOlderPage(sessionId, this.pageSize());
  }

  /** Page back until the whole history is loaded (in-session search). */
  async loadAll(sessionId: string): Promise<void> {
    let t = this.threads.get(sessionId);
    if (!t || t.snap.status !== "ready") {
      await this.loadLatest(sessionId, false);
    }
    for (;;) {
      t = this.threads.get(sessionId);
      if (!t || !t.snap.hasMore || this.disposed) return;
      const before = oldestId(t.snap.messages);
      await this.loadOlderPage(sessionId, LOAD_ALL_PAGE_SIZE);
      const after = this.threads.get(sessionId);
      if (!after || oldestId(after.snap.messages) === before) return;
    }
  }

  evict(sessionId: string): void {
    if (this.lives.has(sessionId) || !this.threads.has(sessionId)) return;
    this.threads.delete(sessionId);
    this.markThread(sessionId);
  }

  // ── Turns ─────────────────────────────────────────────────────────

  /**
   * Send one turn. `ok` is false only when the message never reached the
   * agent (the composer restores the draft); a user Stop resolves ok=true and
   * keeps the partial reply. `sessionId` is the thread the turn now lives in.
   */
  async send(
    sessionId: string | null,
    text: string,
    attachments: ChatAttachment[],
  ): Promise<{ ok: boolean; sessionId: string | null }> {
    const key = keyOf(sessionId);
    if (this.disposed || this.lives.has(key)) return { ok: false, sessionId };
    const clientKey = this.nextClientKey();
    const row: ChatMessage = { role: "user", content: text, clientKey };
    this.patch(key, {
      messages: [...this.thread(key).snap.messages, row],
      error: null,
      note: null,
    });
    const live = this.beginLive(key, null, sessionId, clientKey);
    try {
      const { sessionId: landed } = await streamChatTurn(
        {
          sessionId,
          message: text,
          attachments,
          signal: live.abort.signal,
          profile: this.opts.profile,
        },
        this.handlers(live),
      );
      const readId = this.settle(live, landed || null, true);
      if (readId) void this.reload(readId);
      this.opts.onTurnSettled?.(key, landed || null);
      return { ok: true, sessionId: readId };
    } catch (err) {
      if (isAbort(err)) {
        // Stopped before (or without) the server's ack: keep what streamed.
        const landed = key === NEW_KEY ? live.activity.sessionId : null;
        const readId = this.settle(live, landed, false);
        this.opts.onTurnSettled?.(key, landed);
        return { ok: true, sessionId: readId };
      }
      this.endLive(live);
      const cur = this.thread(key).snap;
      this.patch(key, {
        messages: cur.messages.filter((m) => m.clientKey !== clientKey),
        error: messageOf(err, "The message could not be sent."),
      });
      return { ok: false, sessionId };
    }
  }

  /**
   * Stop the turn for `key`. Closing the connection alone would not stop the
   * server-side turn, so cancel the run and keep the stream for the partial
   * reply; before the server acked the turn (no run id) — or on a second
   * Stop — abort the request instead.
   */
  async stop(key: string): Promise<void> {
    const live = this.lives.get(key);
    if (!live) return;
    const { runId, stopping } = live.activity;
    const sid = live.activity.sessionId ?? (key === NEW_KEY ? null : key);
    if (!runId || !sid || stopping) {
      live.abort.abort();
      return;
    }
    live.activity = { ...live.activity, stopping: true };
    this.markLive(key);
    try {
      await cancelChatTurn({ sessionId: sid, runId, profile: this.opts.profile });
    } catch (err) {
      live.abort.abort();
      this.patch(key, { error: messageOf(err, "The agent could not be stopped.") });
    }
  }

  async resolveApproval(key: string, choice: string): Promise<void> {
    const live = this.lives.get(key);
    const req = live?.approval ?? null;
    if (!live || !req || live.resolving) return;
    live.resolving = true;
    this.markLive(key);
    this.patch(key, { error: null });
    try {
      const res = await fetch("/api/chat/approval", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(
          withProfileBody({ runId: req.runId, choice }, this.opts.profile),
        ),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Your decision could not be submitted.");
      }
      // The turn resumes on the open stream; leave a note of what happens next.
      if (live.approval === req) live.approval = null;
      this.patch(key, { note: decisionText(choice, req) });
    } catch (err) {
      this.patch(key, { error: messageOf(err, "Your decision could not be submitted.") });
    } finally {
      live.resolving = false;
      this.markLive(key);
    }
  }

  /**
   * Attach to a turn running server-side for `sessionId` (started on another
   * surface, or before a reload). Resolves true when a run was attached; the
   * transcript is reconciled afterwards whether or not the attach succeeded.
   */
  async attachIfActive(sessionId: string): Promise<boolean> {
    const key = sessionId;
    if (this.disposed || this.lives.has(key) || this.attaching.has(key)) return false;
    this.attaching.add(key);
    let runId: string;
    try {
      const res = await fetch(
        withProfileQuery(
          `/api/chat/active?sessionId=${encodeURIComponent(sessionId)}`,
          this.opts.profile,
        ),
        { cache: "no-store" },
      );
      const data = (await res.json()) as { runId?: string | null };
      if (!res.ok || !data.runId) return false;
      runId = data.runId;
    } catch {
      return false; // a failed probe is non-fatal — the next one retries
    } finally {
      this.attaching.delete(key);
    }
    if (this.disposed || this.lives.has(key) || this.finishedRuns.get(key) === runId) {
      return false;
    }
    this.patch(key, { error: null, note: null });
    const live = this.beginLive(key, runId, sessionId, null);
    const params: Parameters<typeof attachChatStream>[0] = {
      sessionId,
      runId,
      signal: live.abort.signal,
      profile: this.opts.profile,
    };
    let finished = false;
    try {
      await attachChatStream(params, this.handlers(live));
      finished = true;
    } catch {
      // The grace window may have passed; the reload shows the finished turn.
    }
    this.settle(live, null, finished);
    void this.reload(sessionId);
    this.opts.onTurnSettled?.(key, sessionId);
    return true;
  }

  /**
   * Poll `attachIfActive` every `intervalMs` while the page is visible, on
   * focus / visibilitychange and right after a local turn ends; skipped while
   * that key is busy. Returns the stop function.
   */
  watchActive(sessionId: string, intervalMs: number = ACTIVE_WATCH_MS): () => void {
    let stopped = false;
    let running = false;
    const check = async () => {
      if (stopped || this.disposed || running || this.lives.has(sessionId)) return;
      if (typeof document !== "undefined" && document.visibilityState !== "visible") {
        return;
      }
      running = true;
      try {
        await this.attachIfActive(sessionId);
      } finally {
        running = false;
      }
    };
    const onWake = () => void check();
    void check();
    const timer = setInterval(onWake, intervalMs);
    if (typeof window !== "undefined") window.addEventListener("focus", onWake);
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onWake);
    }
    const offIdle = addSub(this.idleListeners, sessionId, onWake);
    const stop = () => {
      if (stopped) return;
      stopped = true;
      clearInterval(timer);
      if (typeof window !== "undefined") window.removeEventListener("focus", onWake);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onWake);
      }
      offIdle();
      this.watchers.delete(stop);
    };
    this.watchers.add(stop);
    return stop;
  }

  /** Keep alive while mounted; the last release disposes (StrictMode-safe). */
  retain(): () => void {
    this.refs += 1;
    if (this.pendingDispose !== null) {
      clearTimeout(this.pendingDispose);
      this.pendingDispose = null;
    }
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.refs -= 1;
      if (this.refs === 0) {
        this.pendingDispose = setTimeout(() => this.dispose(), 0);
      }
    };
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const stop of [...this.watchers]) stop();
    for (const live of this.lives.values()) live.abort.abort();
    if (this.frame) cancelFrame(this.frame);
    this.frame = null;
    if (this.pendingDispose !== null) clearTimeout(this.pendingDispose);
    this.pendingDispose = null;
  }

  // ── Internals ─────────────────────────────────────────────────────

  private pageSize(): number {
    const n = this.opts.pageSize;
    return n && n > 0 ? Math.min(Math.floor(n), LOAD_ALL_PAGE_SIZE) : TRANSCRIPT_PAGE_SIZE;
  }

  private thread(key: string): ThreadState {
    let t = this.threads.get(key);
    if (!t) {
      t = { snap: emptyThread(key), loadSeq: 0, appliedSeq: 0, older: null };
      this.threads.set(key, t);
    }
    return t;
  }

  private patch(key: string, p: Partial<ThreadSnapshot>): void {
    const t = this.thread(key);
    const cur = t.snap;
    const changed = (Object.keys(p) as (keyof ThreadSnapshot)[]).some(
      (k) => cur[k] !== p[k],
    );
    if (!changed) return;
    t.snap = { ...cur, ...p };
    this.markThread(key);
  }

  private nextClientKey(): string {
    this.optSeq += 1;
    return `${CLIENT_KEY_PREFIX}${this.optSeq}`;
  }

  /**
   * Whether a load that started when `optSeq` was `seqAtStart` must keep an
   * optimistic row: rows of in-flight turns and rows created after the
   * request went out are not in its answer yet.
   */
  private keepOptimistic(m: ChatMessage, seqAtStart: number): boolean {
    if (!m.clientKey) return false;
    if (this.unconfirmed.has(m.clientKey)) return true;
    for (const live of this.lives.values()) {
      if (live.userClientKey === m.clientKey) return true;
    }
    const n = Number(m.clientKey.slice(CLIENT_KEY_PREFIX.length));
    return Number.isFinite(n) && n > seqAtStart;
  }

  private async fetchPage(
    sessionId: string,
    limit: number,
    before?: number,
  ): Promise<{ messages: ChatMessage[]; hasMore: boolean }> {
    const qs =
      `sessionId=${encodeURIComponent(sessionId)}&visible=1&limit=${limit}` +
      (before != null ? `&before=${before}` : "");
    const res = await fetch(
      withProfileQuery(`/api/chat/messages?${qs}`, this.opts.profile),
      { cache: "no-store" },
    );
    const body = (await res.json().catch(() => ({}))) as Partial<ChatMessagesResponse> & {
      detail?: string;
    };
    if (!res.ok) throw new Error(body.detail ?? "Failed to load conversation.");
    return { messages: visibleTurns(body.messages ?? []), hasMore: body.has_more === true };
  }

  private async loadLatest(sessionId: string, quiet: boolean): Promise<void> {
    if (this.disposed) return;
    const t = this.thread(sessionId);
    const seq = ++t.loadSeq;
    const seqAtStart = this.optSeq;
    const current = () => this.threads.get(sessionId) === t && seq > t.appliedSeq;
    try {
      const page = await this.fetchPage(sessionId, this.pageSize());
      if (!current()) return;
      t.appliedSeq = seq;
      const cur = t.snap;
      const { messages, keptOlder } = reconcileTail(cur.messages, page.messages, {
        pageHasMore: page.hasMore,
        keep: (m) => this.keepOptimistic(m, seqAtStart),
      });
      this.ageUnconfirmed(messages);
      this.patch(sessionId, {
        messages,
        hasMore: keptOlder ? cur.hasMore : page.hasMore,
        status: "ready",
      });
    } catch (err) {
      if (quiet || !current()) return;
      this.patch(sessionId, {
        status: t.snap.messages.length > 0 ? "ready" : "error",
        error: messageOf(err, "Failed to load conversation."),
      });
    }
  }

  private loadOlderPage(sessionId: string, limit: number): Promise<void> {
    const t = this.threads.get(sessionId);
    if (!t || this.disposed) return Promise.resolve();
    if (t.older) return t.older;
    const before = oldestId(t.snap.messages);
    if (!t.snap.hasMore || before == null) return Promise.resolve();
    this.patch(sessionId, { loadingOlder: true });
    const live = () => this.threads.get(sessionId) === t;
    t.older = (async () => {
      try {
        const page = await this.fetchPage(sessionId, limit, before);
        // A tail replacement meanwhile moved the oldest row: drop, don't gap.
        if (!live() || oldestId(t.snap.messages) !== before) return;
        this.patch(sessionId, {
          messages: mergeOlderPage(t.snap.messages, page.messages),
          hasMore: page.hasMore,
        });
      } catch (err) {
        if (live()) {
          this.patch(sessionId, { error: messageOf(err, "Failed to load earlier messages.") });
        }
      } finally {
        t.older = null;
        if (live()) this.patch(sessionId, { loadingOlder: false });
      }
    })();
    return t.older;
  }

  private beginLive(
    key: string,
    runId: string | null,
    sessionId: string | null,
    userClientKey: string | null,
  ): LiveState {
    const live = {
      key,
      text: "",
      final: "",
      activity: { ...emptyActivity(runId), sessionId },
      approval: null,
      resolving: false,
      abort: new AbortController(),
      userClientKey,
    } as Omit<LiveState, "snap"> as LiveState;
    live.snap = liveSnapshot(live);
    this.lives.set(key, live);
    this.busy = [...this.busy, key];
    this.markLive(key);
    this.markBusy();
    return live;
  }

  private ageUnconfirmed(messages: ChatMessage[]): void {
    if (this.unconfirmed.size === 0) return;
    const shown = new Set(messages.map((m) => m.clientKey).filter(Boolean));
    for (const [ck, left] of this.unconfirmed) {
      if (!shown.has(ck) || left <= 1) this.unconfirmed.delete(ck);
      else this.unconfirmed.set(ck, left - 1);
    }
  }

  /** `finished`: the stream ran to its end (an aborted run may still be live). */
  private endLive(live: LiveState, finished = false): void {
    if (this.lives.get(live.key) !== live) return;
    this.lives.delete(live.key);
    if (finished && live.activity.runId) {
      this.finishedRuns.set(live.key, live.activity.runId);
    }
    this.busy = this.busy.filter((k) => k !== live.key);
    this.markLive(live.key);
    this.markBusy();
    const listeners = this.idleListeners.get(live.key);
    if (listeners && listeners.size > 0) {
      setTimeout(() => {
        for (const cb of [...listeners]) cb();
      }, 0);
    }
  }

  /**
   * End a turn that reached the agent: append the final reply (never an
   * empty one) and drop the live turn in the same notification, then move the
   * thread to the landed id when adopting it. Returns the id to read from.
   */
  private settle(live: LiveState, landed: string | null, finished: boolean): string | null {
    const key = live.key;
    const content = live.final || live.text;
    if (live.userClientKey) this.unconfirmed.set(live.userClientKey, UNCONFIRMED_RELOADS);
    if (content) {
      const row: ChatMessage = {
        role: "assistant",
        content,
        clientKey: this.nextClientKey(),
      };
      if (live.activity.reasoning) row.reasoning = live.activity.reasoning;
      this.unconfirmed.set(row.clientKey!, UNCONFIRMED_RELOADS);
      this.patch(key, { messages: [...this.thread(key).snap.messages, row] });
    }
    this.endLive(live, finished);
    const own = key === NEW_KEY ? null : key;
    if (!landed || landed === own) return own;
    if (own !== null && !this.opts.adoptLandedSession) return own;
    const src = this.thread(key).snap;
    this.patch(landed, {
      messages: src.messages,
      hasMore: src.hasMore,
      status: "ready",
      error: src.error,
      note: src.note,
    });
    if (key === NEW_KEY) this.patch(key, emptyThread(key));
    return landed;
  }

  private handlers(live: LiveState): ChatStreamHandlers {
    const key = live.key;
    const on = () => this.lives.get(key) === live;
    // Every event bumps lastEventAt so the stall detector only fires on silence.
    const touch = (update: (cur: TurnActivity) => Partial<TurnActivity>) => {
      if (!on()) return;
      live.activity = { ...live.activity, ...update(live.activity), lastEventAt: Date.now() };
      this.markLive(key);
    };
    return {
      onAccepted: (runId, sid) =>
        touch((cur) => ({
          accepted: true,
          runId: runId || cur.runId,
          sessionId: sid || cur.sessionId,
        })),
      onCancelled: () => {
        touch(() => ({ stopping: true }));
        if (on()) this.patch(key, { note: STOPPED_NOTE });
      },
      onDelta: (delta) => {
        if (!on()) return;
        live.text += delta;
        touch(() => ({ accepted: true }));
      },
      onReasoning: (text) =>
        touch((cur) => ({ reasoning: cur.reasoning + text, accepted: true })),
      onToolStart: (tool) =>
        touch((cur) => ({ tools: [...cur.tools, { ...tool, done: false }], accepted: true })),
      onToolComplete: (tool) =>
        touch((cur) => ({
          tools: cur.tools.map((c) => (c.id === tool.id ? { ...c, done: true } : c)),
        })),
      onApproval: (req) => {
        if (!on()) return;
        live.approval = req;
        touch(() => ({}));
      },
      onCompleted: (content) => {
        if (!on()) return;
        live.approval = null;
        if (content) live.final = content;
        this.markLive(key);
      },
      onError: (message) => {
        if (on()) this.patch(key, { error: message });
      },
    };
  }

  private markThread(key: string): void {
    this.dirtyThreads.add(key);
    this.schedule();
  }

  private markLive(key: string): void {
    this.dirtyLive.add(key);
    this.schedule();
  }

  private markBusy(): void {
    this.dirtyBusy = true;
    this.schedule();
  }

  private schedule(): void {
    if (this.frame || this.disposed) return;
    const raf = globalThis.requestAnimationFrame;
    this.frame =
      typeof raf === "function"
        ? { raf: raf(() => this.flush()) }
        : { timer: setTimeout(() => this.flush(), 16) };
  }

  private flush(): void {
    this.frame = null;
    if (this.disposed) return;
    const cbs: (() => void)[] = [];
    if (this.dirtyBusy) cbs.push(...this.busySubs);
    for (const key of this.dirtyThreads) cbs.push(...(this.threadSubs.get(key) ?? []));
    for (const key of this.dirtyLive) {
      const live = this.lives.get(key);
      if (live) live.snap = liveSnapshot(live);
      cbs.push(...(this.liveSubs.get(key) ?? []));
    }
    this.dirtyBusy = false;
    this.dirtyThreads.clear();
    this.dirtyLive.clear();
    for (const cb of cbs) cb();
  }
}

function addSub(map: Map<string, Set<() => void>>, key: string, cb: () => void) {
  let set = map.get(key);
  if (!set) {
    set = new Set();
    map.set(key, set);
  }
  set.add(cb);
  return () => {
    set.delete(cb);
    if (set.size === 0 && map.get(key) === set) map.delete(key);
  };
}

function cancelFrame(frame: Frame): void {
  if ("raf" in frame) globalThis.cancelAnimationFrame?.(frame.raf);
  else clearTimeout(frame.timer);
}

function oldestId(messages: ChatMessage[]): number | undefined {
  return messages.find((m) => m.id != null)?.id;
}

// ── React bindings ──────────────────────────────────────────────────

/** One controller per mount; options track the latest props. */
export function useChatController(opts: ChatControllerOptions): ChatController {
  const [controller] = useState(() => new ChatController(opts));
  useEffect(() => {
    controller.setOptions(opts);
  });
  useEffect(() => controller.retain(), [controller]);
  return controller;
}

export function useThread(c: ChatController, sessionId: string | null): ThreadSnapshot {
  const subscribe = useCallback(
    (cb: () => void) => c.subscribeThread(sessionId, cb),
    [c, sessionId],
  );
  const get = useCallback(() => c.getThread(sessionId), [c, sessionId]);
  return useSyncExternalStore(subscribe, get, get);
}

export function useLiveTurn(c: ChatController, key: string | null): LiveTurnSnapshot | null {
  const subscribe = useCallback(
    (cb: () => void) => (key === null ? () => {} : c.subscribeLive(key, cb)),
    [c, key],
  );
  const get = useCallback(() => (key === null ? null : c.getLive(key)), [c, key]);
  return useSyncExternalStore(subscribe, get, get);
}

export function useBusyKeys(c: ChatController): readonly string[] {
  const subscribe = useCallback((cb: () => void) => c.subscribeBusy(cb), [c]);
  const get = useCallback(() => c.getBusyKeys(), [c]);
  return useSyncExternalStore(subscribe, get, get);
}
