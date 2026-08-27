"use client";

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import Link from "next/link";

import { Composer } from "@/components/chat/Composer";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { StatusIndicator, type ChatActivity } from "@/components/chat/StatusIndicator";
import {
  onLeadChatRequest,
  reportLeadChatOpen,
} from "@/components/coral/coral-interlock";
import { streamChatTurn } from "@/lib/chat/stream";
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
 * floating panel bound to ONE long-running session: the id is pinned in
 * localStorage on first use and reused forever, so the conversation is the
 * same every time. The Python agent core compacts that session's context
 * automatically when it approaches the context window, which is what makes a
 * session long-running rather than long-forgotten.
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
  const [leadSession, setLeadSession] = usePersistentState<string | null>(
    "agent-home:lead-session",
    null,
    (raw) => JSON.parse(raw) as string | null,
    (value) => JSON.stringify(value),
  );
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
  const [approval, setApproval] = useState<ChatApprovalRequest | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fabRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const effectiveRect = dragRect ?? rect;

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
    return { x: origin.x + origin.w - x, y: origin.y + origin.h - y };
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
                One long-running session — context compacts when needed.
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
