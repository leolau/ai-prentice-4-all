"use client";

import { useEffect, useRef, useState } from "react";

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { ConversationList } from "@/components/chat/ConversationList";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { Composer } from "@/components/chat/Composer";
import { StatusIndicator } from "@/components/chat/StatusIndicator";
import {
  setLastAssistantContent,
  withLiveTurn,
  type LiveTurn,
} from "@/lib/chat/messages";
import { streamChatTurn } from "@/lib/chat/stream";
import type {
  ChatApprovalRequest,
  ChatAttachment,
  ChatMessage,
  SessionSummary,
} from "@/types";

/** Map key for a not-yet-created ("New conversation") session. */
const NEW_KEY = "__new__";
const keyOf = (id: string | null): string => id ?? NEW_KEY;

/**
 * The inline confirmation shown after the user answers an approval card, so it
 * is clear what the agent is about to do (or that it was blocked).
 */
function decisionText(choice: string, req: ChatApprovalRequest): string {
  const label = req.command || req.toolName || req.patternKey || "the tool";
  if (choice === "deny") return `Denied — the agent will not run ${label}.`;
  const scope =
    choice === "always"
      ? " (always allowed)"
      : choice === "session"
        ? " (allowed for this chat)"
        : "";
  return `Approved${scope} — running ${label}…`;
}

export interface ChatPaneProps {
  initialSessions: SessionSummary[];
  initialSessionId: string | null;
  initialMessages: ChatMessage[];
  storageEnabled: boolean;
}

/** Only user/assistant turns are shown in the visible thread. */
function visible(messages: ChatMessage[]): ChatMessage[] {
  return messages.filter((m) => m.role === "user" || m.role === "assistant");
}

/** True while the latest assistant turn has streamed no text yet. */
function assistantIsEmpty(messages: ChatMessage[]): boolean {
  const last = messages[messages.length - 1];
  return !last || last.role !== "assistant" || last.content === "";
}

/**
 * FG-20 Wave C1 — the mobile-first one-brain chat pane. A conversation switcher
 * (sheet), a scrollable message thread, and a composer that sends one turn
 * through the `agent-home` BFF (`/api/chat/*`) to the principal-scoped Python
 * endpoint. It never talks to the AI layer or the model loop directly.
 */
export function ChatPane({
  initialSessions,
  initialSessionId,
  initialMessages,
  storageEnabled,
}: ChatPaneProps) {
  const [sessions, setSessions] = useState<SessionSummary[]>(initialSessions);
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId);
  const [messages, setMessages] = useState<ChatMessage[]>(visible(initialMessages));
  const [listOpen, setListOpen] = useState(false);
  const [loadingThread, setLoadingThread] = useState(false);
  // Per-session state, keyed by session id (or NEW_KEY). Turns run per session
  // so the user can switch conversations at any time without cancelling or
  // cross-contaminating an in-flight turn.
  const [sendingKeys, setSendingKeys] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<Record<string, ChatApprovalRequest>>({});
  const [decisions, setDecisions] = useState<Record<string, string>>({});
  const [resolvingApproval, setResolvingApproval] = useState(false);
  const threadRef = useRef<HTMLDivElement | null>(null);
  // Mirrors the selected session for use inside async stream callbacks, and
  // buffers turns whose session is not currently on screen.
  const selectedRef = useRef<string | null>(sessionId);
  const liveRef = useRef<Map<string, LiveTurn>>(new Map());

  useEffect(() => {
    selectedRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sendingKeys]);

  const selKey = keyOf(sessionId);
  const selBusy = sendingKeys.includes(selKey);
  const selApproval = approvals[selKey] ?? null;
  const selDecision = decisions[selKey] ?? null;
  const otherBusy = sendingKeys.some((k) => k !== selKey);

  const removeSending = (k: string) =>
    setSendingKeys((prev) => prev.filter((x) => x !== k));
  function dropKey<T>(rec: Record<string, T>, k: string): Record<string, T> {
    if (!(k in rec)) return rec;
    const next = { ...rec };
    delete next[k];
    return next;
  }

  async function openConversation(id: string) {
    // Switching is allowed at any time — a turn in another session keeps
    // streaming into its own buffer and is overlaid when you return to it.
    setListOpen(false);
    if (id === sessionId) return;
    setSessionId(id);
    selectedRef.current = id;
    setError(null);
    setLoadingThread(true);
    // Show any buffered live turn for this session immediately.
    setMessages(withLiveTurn([], liveRef.current.get(keyOf(id))));
    try {
      const res = await fetch(
        `/api/chat/messages?sessionId=${encodeURIComponent(id)}`,
        { cache: "no-store" },
      );
      const body = (await res.json()) as {
        messages?: ChatMessage[];
        detail?: string;
      };
      if (!res.ok) throw new Error(body.detail ?? "Failed to load conversation.");
      // Re-read the buffer (it may have grown while the transcript loaded) and
      // overlay it onto the persisted history.
      if (selectedRef.current === id) {
        setMessages(
          withLiveTurn(visible(body.messages ?? []), liveRef.current.get(keyOf(id))),
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load conversation.");
    } finally {
      setLoadingThread(false);
    }
  }

  function startNewConversation() {
    setListOpen(false);
    setSessionId(null);
    selectedRef.current = null;
    setMessages(withLiveTurn([], liveRef.current.get(NEW_KEY)));
    setError(null);
  }

  async function send(text: string, attachments: ChatAttachment[]) {
    // The session this turn belongs to, captured up-front so late-arriving
    // events are attributed to their origin, not to whatever is selected later.
    const turnSessionId = sessionId;
    const turnKey = keyOf(turnSessionId);
    if (sendingKeys.includes(turnKey)) return; // one live turn per session
    setError(null);
    setApprovals((prev) => dropKey(prev, turnKey));
    setDecisions((prev) => dropKey(prev, turnKey));
    setSendingKeys((prev) => [...prev, turnKey]);
    // Buffer this turn so it survives session switches; the buffer's assistant
    // text is the single source of truth for accumulated deltas.
    liveRef.current.set(turnKey, { user: text, assistant: "" });
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text },
      { role: "assistant", content: "" },
    ]);

    const onThisSession = () => selectedRef.current === turnSessionId;
    // Update the trailing assistant bubble by position (identity-safe), but
    // only when this turn's session is the one on screen — otherwise just grow
    // the buffer so we never write into another session's thread.
    const setLive = (content: string) => {
      const buf = liveRef.current.get(turnKey);
      if (buf) buf.assistant = content;
      if (onThisSession()) {
        setMessages((prev) => setLastAssistantContent(prev, content));
      }
    };

    try {
      const { sessionId: landed } = await streamChatTurn(
        { sessionId: turnSessionId, message: text, attachments },
        {
          onDelta: (delta) => {
            const buf = liveRef.current.get(turnKey);
            setLive((buf?.assistant ?? "") + delta);
          },
          onApproval: (req) =>
            setApprovals((prev) => ({ ...prev, [turnKey]: req })),
          onCompleted: (content) => {
            setApprovals((prev) => dropKey(prev, turnKey));
            if (content) setLive(content);
          },
          onError: (message) => setError(message),
        },
      );
      // A brand-new session lands an id only at completion; you cannot have
      // navigated to it mid-stream, so adopt it only if still on this turn.
      if (landed && landed !== turnSessionId && onThisSession()) {
        setSessionId(landed);
        selectedRef.current = landed;
      }
      void refreshSessions();
    } catch (err) {
      if (onThisSession()) {
        setMessages((prev) => prev.slice(0, Math.max(0, prev.length - 2)));
      }
      setError(err instanceof Error ? err.message : "The message could not be sent.");
    } finally {
      // Turn is done and now persisted server-side; drop the live buffer so a
      // later re-open shows the canonical transcript, not a duplicate overlay.
      removeSending(turnKey);
      liveRef.current.delete(turnKey);
      setApprovals((prev) => dropKey(prev, turnKey));
    }
  }

  async function resolveApproval(choice: string) {
    const req = selApproval;
    if (!req || resolvingApproval) return;
    const targetKey = selKey;
    setResolvingApproval(true);
    setError(null);
    try {
      const res = await fetch("/api/chat/approval", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ runId: req.runId, choice }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Your decision could not be submitted.");
      }
      // The turn resumes on the open stream. Clear the card and leave an inline
      // note so it is clear what the agent will now do (or that it was denied).
      setApprovals((prev) => dropKey(prev, targetKey));
      setDecisions((prev) => ({ ...prev, [targetKey]: decisionText(choice, req) }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Your decision could not be submitted.");
    } finally {
      setResolvingApproval(false);
    }
  }

  async function refreshSessions() {
    try {
      const res = await fetch("/api/chat/sessions", { cache: "no-store" });
      if (!res.ok) return;
      const body = (await res.json()) as { sessions?: SessionSummary[] };
      if (body.sessions) setSessions(body.sessions);
    } catch {
      // A stale conversation list is non-fatal.
    }
  }

  const activeTitle =
    sessions.find((s) => s.id === sessionId)?.title || "New conversation";
  const sessionCount = sessions.length;

  return (
    <div data-component="ChatPane" className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex items-center gap-2">
        <button
          type="button"
          onClick={() => setListOpen(true)}
          aria-label={`Switch conversation (${sessionCount} chat${sessionCount === 1 ? "" : "s"})`}
          className="flex min-w-0 flex-1 items-center gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-left"
        >
          <span
            aria-hidden="true"
            className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-surface-2)] text-lg leading-none"
          >
            ☰
            {otherBusy ? (
              <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 animate-pulse rounded-full bg-[var(--color-accent)]" />
            ) : null}
          </span>
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="text-[11px] uppercase tracking-wide text-[var(--color-muted)]">
              Conversations · {sessionCount}
            </span>
            <span className="truncate text-sm font-medium text-[var(--color-fg)]">
              {activeTitle}
            </span>
          </span>
          <span aria-hidden="true" className="shrink-0 text-[var(--color-muted)]">
            ⌄
          </span>
        </button>
        <button
          type="button"
          onClick={startNewConversation}
          aria-label="New conversation"
          className="shrink-0 rounded-xl bg-[var(--color-accent)] px-4 py-3 text-sm font-semibold text-[var(--color-accent-fg)]"
        >
          + New
        </button>
      </div>

      <div
        ref={threadRef}
        className="min-h-[42dvh] max-h-[calc(100dvh-19rem)] flex-1 space-y-3 overflow-y-auto rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
      >
        {loadingThread ? (
          <p className="py-8 text-center text-sm text-[var(--color-muted)]">
            Loading conversation…
          </p>
        ) : messages.length === 0 ? (
          <p className="py-8 text-center text-sm text-[var(--color-muted)]">
            {sessionId
              ? "No messages yet — say hello."
              : "Start a new conversation with your agent."}
          </p>
        ) : (
          messages
            .filter((m) => m.role !== "assistant" || m.content !== "")
            .map((m, i) => <MessageBubble key={m.id ?? i} message={m} />)
        )}
        {selDecision ? (
          <div
            data-component="DecisionNotice"
            className="flex justify-start"
          >
            <span className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-1.5 text-xs text-[var(--color-muted)]">
              {selDecision}
            </span>
          </div>
        ) : null}
        <StatusIndicator
          activity={
            selApproval
              ? "waiting_approval"
              : selBusy
                ? assistantIsEmpty(messages)
                  ? "thinking"
                  : "streaming"
                : "idle"
          }
        />
      </div>

      {selApproval ? (
        <ApprovalCard
          request={selApproval}
          busy={resolvingApproval}
          onResolve={resolveApproval}
        />
      ) : null}

      {error ? (
        <p className="mt-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm text-red-300">
          {error}
        </p>
      ) : null}

      <Composer sending={selBusy} storageEnabled={storageEnabled} sessionId={sessionId} onSend={send} />

      {listOpen ? (
        <ConversationList
          sessions={sessions}
          activeId={sessionId}
          onSelect={openConversation}
          onClose={() => setListOpen(false)}
        />
      ) : null}
    </div>
  );
}
