"use client";

import { useEffect, useRef, useState } from "react";

import { Composer } from "@/components/chat/Composer";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { StatusIndicator, type ChatActivity } from "@/components/chat/StatusIndicator";
import { streamChatTurn } from "@/lib/chat/stream";
import { usePersistentState } from "@/lib/use-persistent-state";
import type { ChatApprovalRequest, ChatAttachment, ChatMessage } from "@/types";

/**
 * Lead chat — the second Coral floating button (bottom-right). Opens a
 * floating panel bound to ONE long-running session: the id is pinned in
 * localStorage on first use and reused forever, so the conversation is the
 * same every time. The Python agent core compacts that session's context
 * automatically when it approaches the context window, which is what makes a
 * session long-running rather than long-forgotten.
 */
export function LeadChatHost() {
  const [open, setOpen] = useState(false);
  const [leadSession, setLeadSession] = usePersistentState<string | null>(
    "agent-home:lead-session",
    null,
    (raw) => JSON.parse(raw) as string | null,
    (value) => JSON.stringify(value),
  );
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [activity, setActivity] = useState<ChatActivity>("idle");
  const [streamText, setStreamText] = useState("");
  const [approval, setApproval] = useState<ChatApprovalRequest | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fabRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open || !leadSession) return;
    let cancelled = false;
    setLoading(true);
    fetch(`/api/chat/messages?sessionId=${encodeURIComponent(leadSession)}`)
      .then((res) => (res.ok ? res.json() : { messages: [] }))
      .then((data: { messages?: ChatMessage[] }) => {
        if (!cancelled) setMessages(data.messages ?? []);
      })
      .catch(() => {
        if (!cancelled) setMessages([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, leadSession]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, streamText, activity, open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        fabRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  async function send(text: string, attachments: ChatAttachment[]) {
    if (sending) return;
    setSending(true);
    setActivity("thinking");
    setStreamText("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    try {
      await streamChatTurn(
        { sessionId: leadSession, message: text, attachments },
        {
          onDelta: (delta) => {
            setActivity("streaming");
            setStreamText((prev) => prev + delta);
          },
          onApproval: (req) => {
            setActivity("waiting_approval");
            setApproval(req);
          },
          onCompleted: (content, sessionId) => {
            setMessages((prev) => [...prev, { role: "assistant", content }]);
            if (sessionId) setLeadSession(sessionId);
            setStreamText("");
            setApproval(null);
          },
          onError: (message) => {
            setMessages((prev) => [...prev, { role: "assistant", content: message }]);
            setStreamText("");
          },
        },
      );
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: err instanceof Error ? err.message : "The turn failed.",
        },
      ]);
    } finally {
      setSending(false);
      setActivity("idle");
    }
  }

  async function resolveApproval(choice: "once" | "deny") {
    if (!approval) return;
    const runId = approval.runId;
    setApproval(null);
    setActivity("streaming");
    await fetch("/api/chat/approval", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ runId, choice }),
    }).catch(() => undefined);
  }

  return (
    <div data-component="LeadChatHost">
      {open ? (
        <>
          <div
            className="coral-backdrop fixed inset-0 z-40 bg-black/60"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <div className="leadchat-panel" role="dialog" aria-label="Lead chat">
            <div className="mb-2 flex items-center justify-between px-1">
              <div>
                <h3 className="text-sm font-semibold">Lead chat</h3>
                <p className="text-[11px] text-[var(--color-muted)]">
                  One long-running session — context compacts when needed.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close lead chat"
                className="text-sm text-[var(--color-muted)]"
              >
                Close
              </button>
            </div>
            <div ref={scrollRef} className="leadchat-scroll">
              {loading ? (
                <p className="text-xs text-[var(--color-muted)]">Loading the conversation…</p>
              ) : messages.length === 0 && !streamText ? (
                <p className="text-xs text-[var(--color-muted)]">
                  The lead session starts with your first message and keeps
                  running from there.
                </p>
              ) : (
                messages.map((m, i) => <MessageBubble key={i} message={m} msgIndex={i} />)
              )}
              {streamText ? (
                <div className="mt-2 whitespace-pre-wrap rounded-xl bg-[var(--color-surface-2)] px-3 py-2 text-sm">
                  {streamText}
                </div>
              ) : null}
              <StatusIndicator activity={activity} />
              {approval ? (
                <div className="mt-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-2)] p-2 text-xs">
                  <p className="mb-1">
                    Approve <code>{approval.toolName ?? "tool"}</code>
                    {approval.description ? ` — ${approval.description}` : ""}
                  </p>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => void resolveApproval("once")}
                      className="rounded-lg bg-[var(--color-accent)] px-2 py-1 text-[var(--color-accent-fg)]"
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      onClick={() => void resolveApproval("deny")}
                      className="rounded-lg border border-[var(--color-border)] px-2 py-1"
                    >
                      Deny
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
            <Composer
              sending={sending}
              storageEnabled={false}
              sessionId={leadSession}
              onSend={(text, attachments) => void send(text, attachments)}
            />
          </div>
        </>
      ) : null}
      <button
        ref={fabRef}
        type="button"
        onClick={() => setOpen((cur) => !cur)}
        aria-expanded={open}
        aria-label={open ? "Close lead chat" : "Open lead chat"}
        className="coral-fab fixed z-[60] flex h-14 w-14 items-center justify-center rounded-full text-xl text-[var(--color-accent-fg)]"
        style={{
          right: "1rem",
          bottom: "calc(var(--safe-bottom) + 1rem)",
          background:
            "linear-gradient(135deg, var(--color-accent), color-mix(in srgb, var(--color-accent) 60%, #ff7e6b))",
        }}
      >
        <span aria-hidden>{open ? "✕" : "✦"}</span>
      </button>
    </div>
  );
}
