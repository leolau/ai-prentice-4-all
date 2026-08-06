"use client";

import { useEffect, useRef, useState } from "react";

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { ConversationList } from "@/components/chat/ConversationList";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { Composer } from "@/components/chat/Composer";
import { StatusIndicator } from "@/components/chat/StatusIndicator";
import { setLastAssistantContent } from "@/lib/chat/messages";
import { streamChatTurn } from "@/lib/chat/stream";
import type {
  ChatApprovalRequest,
  ChatAttachment,
  ChatMessage,
  SessionSummary,
} from "@/types";

/** A pending approval, bound to the run AND the session that raised it. */
interface PendingApproval {
  request: ChatApprovalRequest;
  sessionId: string | null;
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
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approval, setApproval] = useState<PendingApproval | null>(null);
  const [resolvingApproval, setResolvingApproval] = useState(false);
  const threadRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  async function openConversation(id: string) {
    // Never switch mid-turn: a live stream writes into the active thread, so
    // switching would splice its deltas / approval into the wrong session.
    if (sending) return;
    setListOpen(false);
    setApproval(null);
    if (id === sessionId) return;
    setSessionId(id);
    setError(null);
    setLoadingThread(true);
    setMessages([]);
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
      setMessages(visible(body.messages ?? []));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load conversation.");
    } finally {
      setLoadingThread(false);
    }
  }

  function startNewConversation() {
    if (sending) return;
    setListOpen(false);
    setApproval(null);
    setSessionId(null);
    setMessages([]);
    setError(null);
  }

  async function send(text: string, attachments: ChatAttachment[]) {
    if (sending) return;
    setError(null);
    setApproval(null);
    setSending(true);
    // The session this turn belongs to, captured up-front so a late-arriving
    // event is attributed to its origin, not to whatever is selected later.
    const turnSessionId = sessionId;
    const optimistic: ChatMessage = { role: "user", content: text };
    setMessages((prev) => [
      ...prev,
      optimistic,
      { role: "assistant", content: "" },
    ]);

    // Update the trailing assistant bubble by position (identity-safe): the
    // first delta replaces the placeholder object, so an identity match would
    // stop firing and truncate the reply to its first token.
    let assistantText = "";
    const setLive = (content: string) =>
      setMessages((prev) => setLastAssistantContent(prev, content));

    try {
      const { sessionId: landed } = await streamChatTurn(
        { sessionId, message: text, attachments },
        {
          onDelta: (delta) => {
            assistantText += delta;
            setLive(assistantText);
          },
          onApproval: (req) =>
            setApproval({ request: req, sessionId: turnSessionId }),
          onCompleted: (content) => {
            setApproval(null);
            if (content) setLive(content);
          },
          onError: (message) => setError(message),
        },
      );
      if (landed && landed !== turnSessionId) setSessionId(landed);
      void refreshSessions();
    } catch (err) {
      // Drop this turn's optimistic pair (the two trailing messages we added).
      setMessages((prev) => prev.slice(0, Math.max(0, prev.length - 2)));
      setError(err instanceof Error ? err.message : "The message could not be sent.");
    } finally {
      setSending(false);
      setApproval(null);
    }
  }

  async function resolveApproval(choice: string) {
    if (!approval || resolvingApproval) return;
    setResolvingApproval(true);
    setError(null);
    try {
      const res = await fetch("/api/chat/approval", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ runId: approval.request.runId, choice }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Your decision could not be submitted.");
      }
      // The turn resumes on the open stream; clear the card and let deltas flow.
      setApproval(null);
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
          disabled={sending}
          aria-label="Switch conversation"
          className="flex min-w-0 flex-1 items-center gap-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-left text-sm disabled:opacity-50"
        >
          <span aria-hidden="true">☰</span>
          <span className="min-w-0 flex-1 truncate">{activeTitle}</span>
          <span className="shrink-0 rounded-full bg-[var(--color-surface-2)] px-2 py-0.5 text-xs text-[var(--color-muted)]">
            {sessionCount} chat{sessionCount === 1 ? "" : "s"}
          </span>
        </button>
        <button
          type="button"
          onClick={startNewConversation}
          disabled={sending}
          className="shrink-0 rounded-xl bg-[var(--color-accent)] px-3 py-2 text-sm font-semibold text-[var(--color-accent-fg)] disabled:opacity-50"
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
        <StatusIndicator
          activity={
            approval
              ? "waiting_approval"
              : sending
                ? assistantIsEmpty(messages)
                  ? "thinking"
                  : "streaming"
                : "idle"
          }
        />
      </div>

      {approval && approval.sessionId === sessionId ? (
        <ApprovalCard
          request={approval.request}
          busy={resolvingApproval}
          onResolve={resolveApproval}
        />
      ) : null}

      {error ? (
        <p className="mt-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm text-red-300">
          {error}
        </p>
      ) : null}

      <Composer sending={sending} storageEnabled={storageEnabled} sessionId={sessionId} onSend={send} />

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
