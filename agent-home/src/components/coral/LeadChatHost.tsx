"use client";

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import Link from "next/link";

import { Composer } from "@/components/chat/Composer";
import { LiveActivity } from "@/components/chat/LiveActivity";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { StatusIndicator, type ChatActivity } from "@/components/chat/StatusIndicator";
import {
  onLeadChatRequest,
  reportLeadChatOpen,
} from "@/components/coral/coral-interlock";
import {
  attachChatStream,
  streamChatTurn,
  type ChatStreamHandlers,
  type ChatToolEvent,
} from "@/lib/chat/stream";
import { visibleTurns } from "@/lib/chat/transcript";
import { usePersistentState } from "@/lib/use-persistent-state";
import type { ChatApprovalRequest, ChatAttachment, ChatMessage } from "@/types";

/** Where the floating panel sits and how big it is, once the user moves it. */
interface LeadChatRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

const MIN_W = 260;
const MIN_H = 240;
const EDGE = 8;

/**
 * Lead chat — the second Coral floating button (bottom-right). Opens a
 * floating panel bound to ONE long-running session, resolved from the server
 * (`GET /api/chat/lead`) rather than pinned in this browser: the id is derived
 * from the signed-in principal, so a phone and a desktop open the *same*
 * conversation and a turn still running when you put the phone down is there,
 * mid-flight, when you sign in on the desktop. The Python agent core compacts
 * that session's context automatically when it approaches the context window,
 * which is what makes a session long-running rather than long-forgotten.
 *
 * The panel is a floating window, not a modal: drag the header to move it,
 * drag either corner grip (bottom-right or upper-left) to resize it. The
 * chosen position and size are persisted (`agent-home:leadchat-rect`) and
 * restored the next time it opens.
 */
export function LeadChatHost({
  storageEnabled = false,
}: {
  /** Whether Supabase Storage is configured on the box — gates the attach button. */
  storageEnabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [leadSession, setLeadSession] = useState<string | null>(null);
  const [rect, setRect] = usePersistentState<LeadChatRect | null>(
    "agent-home:leadchat-rect",
    null,
    (raw) => JSON.parse(raw) as LeadChatRect | null,
    (value) => JSON.stringify(value),
  );
  const [dragRect, setDragRect] = useState<LeadChatRect | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [activity, setActivity] = useState<ChatActivity>("idle");
  const [streamText, setStreamText] = useState("");
  const [reasoningText, setReasoningText] = useState("");
  const [toolChips, setToolChips] = useState<(ChatToolEvent & { done: boolean })[]>([]);
  const [approval, setApproval] = useState<ChatApprovalRequest | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fabRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  /** Bumped on every turn started here, so a slow history load can tell it is stale. */
  const turnsRef = useRef(0);

  const effectiveRect = dragRect ?? rect;

  // Which conversation this panel is. Asked once the panel opens, so a
  // signed-in page that never opens the lead chat creates no session.
  useEffect(() => {
    if (!open || leadSession) return;
    let cancelled = false;
    void resolveLeadSession().then((sid) => {
      if (!cancelled && sid) setLeadSession(sid);
    });
    return () => {
      cancelled = true;
    };
  }, [open, leadSession]);

  useEffect(() => {
    if (!open || !leadSession) return;
    let cancelled = false;
    // A turn begun while this load is in flight is newer than the transcript
    // it answers with; applying it would erase what the user just sent.
    const turnsAtStart = turnsRef.current;
    const stale = () => cancelled || turnsRef.current !== turnsAtStart;
    setLoading(true);
    fetch(`/api/chat/messages?sessionId=${encodeURIComponent(leadSession)}`)
      .then((res) => (res.ok ? res.json() : { messages: [] }))
      .then((data: { messages?: ChatMessage[] }) => {
        if (!stale()) setMessages(visibleTurns(data.messages ?? []));
      })
      .catch(() => {
        if (!stale()) setMessages([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, leadSession]);

  // Reload mid-turn: the server-side turn outlives the page, so re-attach
  // to its stream and keep the content flowing instead of staring at a
  // transcript that ends at the user message.
  useEffect(() => {
    if (!open || !leadSession || sending) return;
    let cancelled = false;
    fetch(`/api/chat/active?sessionId=${encodeURIComponent(leadSession)}`)
      .then((res) => (res.ok ? res.json() : { runId: null }))
      .then((data: { runId?: string | null }) => {
        if (cancelled || !data.runId) return;
        const runId = data.runId;
        setSending(true);
        setActivity("thinking");
        setStreamText("");
        setReasoningText("");
        setToolChips([]);
        attachChatStream({ sessionId: leadSession, runId }, makeHandlers())
          .catch(() => undefined)
          .finally(() => {
            setSending(false);
            setActivity("idle");
            setReasoningText("");
            setToolChips([]);
          });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [open, leadSession, sending]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, streamText, reasoningText, toolChips, activity, open]);

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

  // Interlock with the launcher menu (coral-interlock): it parks this panel
  // while the menu is up and opens it back up afterwards.
  useEffect(() => {
    reportLeadChatOpen(open);
    return () => reportLeadChatOpen(false);
  }, [open]);

  useEffect(() => onLeadChatRequest((requested) => setOpen(requested)), []);

  /** The panel's current box — measured from the DOM until the user moves it. */
  function originRect(): LeadChatRect {
    if (effectiveRect) return effectiveRect;
    const b = panelRef.current?.getBoundingClientRect();
    const origin = b
      ? { x: b.left, y: b.top, w: b.width, h: b.height }
      : { x: EDGE * 1.5, y: 96, w: 0, h: 0 };
    // A measured box can be smaller than the minimums (mid-animation, or a
    // test DOM that reports zeros) — clamp up so a first drag never strands
    // the panel undersized.
    if (origin.w < MIN_W) origin.w = 360;
    if (origin.h < MIN_H) origin.h = 420;
    return origin;
  }

  function computeRect(
    mode: "move" | "resize-br" | "resize-tl",
    origin: LeadChatRect,
    sx: number,
    sy: number,
    cx: number,
    cy: number,
  ): LeadChatRect {
    const dx = cx - sx;
    const dy = cy - sy;
    if (mode === "move") {
      const x = Math.min(
        Math.max(EDGE, origin.x + dx),
        Math.max(EDGE, window.innerWidth - origin.w - EDGE),
      );
      const y = Math.min(
        Math.max(EDGE, origin.y + dy),
        Math.max(EDGE, window.innerHeight - 48),
      );
      return { x, y, w: origin.w, h: origin.h };
    }
    if (mode === "resize-br") {
      const w = Math.min(Math.max(MIN_W, origin.w + dx), window.innerWidth - 2 * EDGE);
      const h = Math.min(Math.max(MIN_H, origin.h + dy), window.innerHeight - 2 * EDGE);
      return { x: origin.x, y: origin.y, w, h };
    }
    // Upper-left grip: the bottom-right corner stays anchored while the
    // top-left edge follows the pointer.
    const x = Math.min(Math.max(EDGE, origin.x + dx), origin.x + origin.w - MIN_W);
    const y = Math.min(Math.max(EDGE, origin.y + dy), origin.y + origin.h - MIN_H);
    return { x, y, w: origin.x + origin.w - x, h: origin.y + origin.h - y };
  }

  /** Pointer-down starter for the header (move) or a corner grip (resize). */
  function startPointer(mode: "move" | "resize-br" | "resize-tl") {
    return (e: ReactPointerEvent) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault();
      const origin = originRect();
      const sx = e.clientX;
      const sy = e.clientY;
      const onMove = (ev: PointerEvent) => {
        setDragRect(computeRect(mode, origin, sx, sy, ev.clientX, ev.clientY));
      };
      const onUp = (ev: PointerEvent) => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        setDragRect(null);
        // Commit the final box to localStorage so it survives reloads.
        setRect(computeRect(mode, origin, sx, sy, ev.clientX, ev.clientY));
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    };
  }

  function makeHandlers(): ChatStreamHandlers {
    return {
      onDelta: (delta) => {
        setActivity("streaming");
        setStreamText((prev) => prev + delta);
      },
      onReasoning: (textDelta) => {
        setActivity("thinking");
        setReasoningText((prev) => prev + textDelta);
      },
      onToolStart: (tool) => {
        setActivity("streaming");
        setToolChips((prev) => [...prev, { ...tool, done: false }]);
      },
      onToolComplete: (tool) => {
        setToolChips((prev) =>
          prev.map((c) => (c.id === tool.id ? { ...c, done: true } : c)),
        );
      },
      onApproval: (req) => {
        setActivity("waiting_approval");
        setApproval(req);
      },
      onCompleted: (content) => {
        // Deliberately does NOT re-pin to the id the turn reports: a compacted
        // conversation answers under a continuation id, and adopting it here
        // would make this browser's lead chat diverge from every other one.
        // Every read path resolves the chain from the root id.
        setMessages((prev) => [...prev, { role: "assistant", content }]);
        setStreamText("");
        setReasoningText("");
        setToolChips([]);
        setApproval(null);
      },
      onError: (message) => {
        setMessages((prev) => [...prev, { role: "assistant", content: message }]);
        setStreamText("");
        setReasoningText("");
        setToolChips([]);
      },
    };
  }

  /** The server's answer to "which conversation am I", or null if it can't say. */
  async function resolveLeadSession(): Promise<string | null> {
    try {
      const res = await fetch("/api/chat/lead");
      if (!res.ok) return null;
      const data = (await res.json()) as { sessionId?: string | null };
      return data.sessionId ?? null;
    } catch {
      return null;
    }
  }

  async function send(text: string, attachments: ChatAttachment[]) {
    if (sending) return;
    // Sending with no session id would start a *new* conversation, which is
    // the bug this panel used to have in a different shape: a lead chat that
    // is only this browser's. Better to say so than to fork it silently.
    const sessionId = leadSession ?? (await resolveLeadSession());
    if (!sessionId) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            "The lead conversation could not be reached, so this message was not sent. Try again in a moment.",
        },
      ]);
      return;
    }
    if (sessionId !== leadSession) setLeadSession(sessionId);
    turnsRef.current += 1;
    setSending(true);
    setActivity("thinking");
    setStreamText("");
    setReasoningText("");
    setToolChips([]);
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    try {
      await streamChatTurn(
        { sessionId, message: text, attachments },
        makeHandlers(),
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
      setReasoningText("");
      setToolChips([]);
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
        <div
          ref={panelRef}
          className="leadchat-panel"
          role="dialog"
          aria-label="Lead chat"
          style={
            effectiveRect
              ? {
                  left: effectiveRect.x,
                  top: effectiveRect.y,
                  right: "auto",
                  bottom: "auto",
                  width: effectiveRect.w,
                  height: effectiveRect.h,
                }
              : undefined
          }
        >
          <div className="mb-2 flex items-start justify-between gap-2 px-1">
            <div
              className="leadchat-drag min-w-0 flex-1"
              onPointerDown={startPointer("move")}
              title="Drag to move"
            >
              <h3 className="text-sm font-semibold">Lead chat</h3>
              <p className="text-[11px] text-[var(--color-muted)]">
                One long-running session, the same on every device.
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2 text-sm">
              <Link
                href="/chat"
                className="rounded-lg border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-muted)]"
              >
                Chats
              </Link>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="text-[var(--color-muted)]"
              >
                Close
              </button>
            </div>
          </div>
          <div
            ref={scrollRef}
            className="leadchat-scroll"
            style={
              effectiveRect ? { maxHeight: "none", flex: 1, minHeight: 0 } : undefined
            }
          >
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
            <LiveActivity reasoning={reasoningText} tools={toolChips} />
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
            storageEnabled={storageEnabled}
            sessionId={leadSession}
            onSend={(text, attachments) => void send(text, attachments)}
          />
          <div
            className="leadchat-resize-tl"
            onPointerDown={startPointer("resize-tl")}
            title="Drag to resize"
            aria-hidden
          />
          <div
            className="leadchat-resize"
            onPointerDown={startPointer("resize-br")}
            title="Drag to resize"
            aria-hidden
          />
        </div>
      ) : null}
      <button
        ref={fabRef}
        type="button"
        onClick={() => setOpen((cur) => !cur)}
        aria-expanded={open}
        aria-label={open ? "Close lead chat" : "Open lead chat"}
        hidden={open}
        className="coral-fab fixed z-[60] flex h-14 w-14 items-center justify-center rounded-full text-xl text-[var(--color-accent-fg)]"
        style={{
          right: "1rem",
          bottom: "calc(var(--safe-bottom) + 1rem)",
          background:
            "linear-gradient(135deg, var(--color-accent), color-mix(in srgb, var(--color-accent) 60%, #ff7e6b))",
        }}
      >
        <span aria-hidden>✦</span>
      </button>
    </div>
  );
}
