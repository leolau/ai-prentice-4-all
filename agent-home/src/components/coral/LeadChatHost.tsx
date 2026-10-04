"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type PointerEvent as ReactPointerEvent,
} from "react";
import Link from "next/link";

import "./coral.css";

import { Composer } from "@/components/chat/Composer";
import { ChatThread } from "@/components/chat/thread/ChatThread";
import {
  onLeadChatRequest,
  reportLeadChatOpen,
} from "@/components/coral/coral-interlock";
import {
  keyOf,
  useBusyKeys,
  useChatController,
  type ChatController,
} from "@/lib/chat/chat-controller";
import { usePersistentState } from "@/lib/use-persistent-state";
import type { ChatAttachment } from "@/types";

/** Where the floating panel sits and how big it is, once the user moves it. */
interface LeadChatRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

type DragMode = "move" | "resize-br" | "resize-tl";
type Snap = "half" | "full";

const RECT_KEY = "agent-home:leadchat-rect";
const MIN_W = 260;
const MIN_H = 240;
const EDGE = 8;
/** Floating panel at and above this width; bottom sheet below it. */
const DESKTOP_QUERY = "(min-width: 768px)";
/** Sheet snap heights as a fraction of the viewport (CSS: 55dvh / 92dvh). */
const SNAP_HALF = 0.55;
const SNAP_FULL = 0.92;
/** A sheet dragged below this fraction of the viewport closes. */
const SHEET_CLOSE = 0.3;
/** Pointer travel (px) below which a grab-handle press is a tap. */
const TAP_SLOP = 6;

const REFUSED_TEXT =
  "The lead conversation could not be reached, so this message was not sent. Try again in a moment.";

const parseRect = (raw: string) => JSON.parse(raw) as LeadChatRect | null;
const serializeRect = (value: LeadChatRect | null) => JSON.stringify(value);

/** rAF with a timer fallback; returns its cancel function. */
function nextFrame(cb: () => void): () => void {
  if (typeof requestAnimationFrame === "function") {
    const id = requestAnimationFrame(cb);
    return () => cancelAnimationFrame(id);
  }
  const id = setTimeout(cb, 16);
  return () => clearTimeout(id);
}

/** SSR-safe media query: the server (and a DOM without matchMedia) gets `fallback`. */
function useMediaQuery(query: string, fallback: boolean): boolean {
  const subscribe = useCallback(
    (cb: () => void) => {
      if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
        return () => {};
      }
      const mql = window.matchMedia(query);
      mql.addEventListener?.("change", cb);
      return () => mql.removeEventListener?.("change", cb);
    },
    [query],
  );
  const get = useCallback(
    () =>
      typeof window !== "undefined" && typeof window.matchMedia === "function"
        ? window.matchMedia(query).matches
        : fallback,
    [query, fallback],
  );
  return useSyncExternalStore(subscribe, get, () => fallback);
}

/** Whether the turn for `key` is winding down after a Stop — a primitive, so streaming never re-renders the host. */
function useStopping(c: ChatController, key: string): boolean {
  const subscribe = useCallback((cb: () => void) => c.subscribeLive(key, cb), [c, key]);
  const get = useCallback(() => c.getLive(key)?.activity.stopping ?? false, [c, key]);
  return useSyncExternalStore(subscribe, get, () => false);
}

function computeRect(
  mode: DragMode,
  origin: LeadChatRect,
  dx: number,
  dy: number,
): LeadChatRect {
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

/**
 * Fit a saved box into the current viewport (window resized smaller), with
 * the same bounds a drag allows: fully inside horizontally, header on screen.
 */
function clampRect(r: LeadChatRect): LeadChatRect {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const w = Math.max(Math.min(r.w, vw - 2 * EDGE), Math.min(MIN_W, vw - 2 * EDGE));
  const h = Math.max(Math.min(r.h, vh - 2 * EDGE), Math.min(MIN_H, vh - 2 * EDGE));
  const x = Math.min(Math.max(EDGE, r.x), Math.max(EDGE, vw - w - EDGE));
  const y = Math.min(Math.max(EDGE, r.y), Math.max(EDGE, vh - 48));
  return { x, y, w, h };
}

function setBox(el: HTMLElement, r: LeadChatRect): void {
  el.style.left = `${r.x}px`;
  el.style.top = `${r.y}px`;
  el.style.right = "auto";
  el.style.bottom = "auto";
  el.style.width = `${r.w}px`;
  el.style.height = `${r.h}px`;
}

/** The server's answer to "which conversation am I", or null if it can't say. */
async function fetchLeadSession(): Promise<string | null> {
  try {
    const res = await fetch("/api/chat/lead");
    if (!res.ok) return null;
    const data = (await res.json()) as { sessionId?: string | null };
    return data.sessionId ?? null;
  } catch {
    return null;
  }
}

/**
 * Lead chat — the second Coral floating button (bottom-right). Opens a
 * panel bound to ONE long-running session, resolved from the server
 * (`GET /api/chat/lead`) rather than pinned in this browser: the id is derived
 * from the signed-in principal, so a phone and a desktop open the *same*
 * conversation and a turn still running when you put the phone down is there,
 * mid-flight, when you sign in on the desktop.
 *
 * It runs on the shared chat engine (`ChatController` + `ChatThread`), so
 * streaming re-renders only the live reply. Minimising hides the panel but
 * keeps it mounted — transcript, draft and scroll survive and reopening does
 * not refetch; a reply that lands meanwhile puts an unread dot on the FAB.
 *
 * Desktop: a floating window — drag the header to move it, either corner
 * grip to resize it; the box is persisted (`agent-home:leadchat-rect`).
 * Phone (<768px): a bottom sheet with half / full snap points on its handle.
 */
export function LeadChatHost({
  storageEnabled = false,
}: {
  /** Whether Supabase Storage is configured on the box — gates the attach button. */
  storageEnabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  /** Mounted on first open, then kept (hidden) so nothing is lost on close. */
  const [mounted, setMounted] = useState(false);
  const [unread, setUnread] = useState(false);
  const [leadSession, setLeadSession] = useState<string | null>(null);
  const [resolveFailed, setResolveFailed] = useState(false);
  const [refused, setRefused] = useState(false);
  const [resolvingSend, setResolvingSend] = useState(false);
  const [snap, setSnap] = useState<Snap>("half");
  const [rect, setRect] = usePersistentState<LeadChatRect | null>(
    RECT_KEY,
    null,
    parseRect,
    serializeRect,
  );
  const desktop = useMediaQuery(DESKTOP_QUERY, true);

  const panelRef = useRef<HTMLDivElement>(null);
  const fabRef = useRef<HTMLButtonElement>(null);
  const openRef = useRef(open);
  const focusFabRef = useRef(false);
  const openedThreadRef = useRef(false);
  const resolveRef = useRef<Promise<string | null> | null>(null);
  const suppressClickRef = useRef(false);

  const controller = useChatController({
    // A compacted conversation answers under a continuation id; adopting it
    // would fork this browser's lead chat from every other one.
    adoptLandedSession: false,
    onTurnSettled: () => {
      if (!openRef.current) setUnread(true);
    },
  });
  const key = keyOf(leadSession);
  const busy = useBusyKeys(controller).includes(key);
  const stopping = useStopping(controller, key);

  const show = useCallback(() => {
    setMounted(true);
    setOpen(true);
    setUnread(false);
  }, []);

  const hide = useCallback((restoreFocus: boolean) => {
    focusFabRef.current = restoreFocus;
    setOpen(false);
  }, []);

  useEffect(() => {
    openRef.current = open;
    if (open) {
      panelRef.current?.focus({ preventScroll: true });
    } else if (focusFabRef.current) {
      focusFabRef.current = false;
      fabRef.current?.focus();
    }
  }, [open]);

  /** One in-flight resolution shared by the open effect and send. */
  const resolveLead = useCallback((): Promise<string | null> => {
    resolveRef.current ??= fetchLeadSession().then((sid) => {
      resolveRef.current = null;
      if (sid) setLeadSession(sid);
      else setResolveFailed(true);
      return sid;
    });
    return resolveRef.current;
  }, []);

  // Which conversation this panel is. Asked once the panel opens, so a
  // signed-in page that never opens the lead chat creates no session.
  useEffect(() => {
    if (open && !leadSession) void resolveLead();
  }, [open, leadSession, resolveLead]);

  // First page once, as soon as the id is known (even if the panel was
  // closed meanwhile); reopening serves the kept thread without a refetch.
  useEffect(() => {
    if (!leadSession || openedThreadRef.current) return;
    openedThreadRef.current = true;
    void controller.open(leadSession);
  }, [leadSession, controller]);

  // Every open probes for a turn another device left running, and keeps
  // watching while the panel is up.
  useEffect(() => {
    if (!open || !leadSession) return;
    void controller.attachIfActive(leadSession);
    return controller.watchActive(leadSession);
  }, [open, leadSession, controller]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") hide(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, hide]);

  // Interlock with the launcher menu (coral-interlock): it parks this panel
  // while the menu is up and opens it back up afterwards.
  useEffect(() => {
    reportLeadChatOpen(open);
    return () => reportLeadChatOpen(false);
  }, [open]);

  useEffect(
    () => onLeadChatRequest((requested) => (requested ? show() : hide(false))),
    [show, hide],
  );

  // Keep a saved box inside the viewport when the window shrinks.
  useEffect(() => {
    if (!open || !desktop || !rect) return;
    let cancel: (() => void) | null = null;
    const fit = () => {
      cancel = null;
      const next = clampRect(rect);
      if (next.x !== rect.x || next.y !== rect.y || next.w !== rect.w || next.h !== rect.h) {
        setRect(next);
      }
    };
    const onResize = () => {
      cancel ??= nextFrame(fit);
    };
    fit();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      cancel?.();
    };
  }, [open, desktop, rect, setRect]);

  /** The panel's current box — measured from the DOM until the user moves it. */
  function originRect(): LeadChatRect {
    if (rect) return rect;
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

  /**
   * Header (move) or corner grip (resize). Pointer moves write the transform
   * and size straight to the DOM once per frame; React state and the
   * persisted box change only on release.
   */
  function startPointer(mode: DragMode) {
    return (e: ReactPointerEvent) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      const el = panelRef.current;
      if (!el) return;
      e.preventDefault();
      const origin = originRect();
      const sx = e.clientX;
      const sy = e.clientY;
      let latest = origin;
      let cancel: (() => void) | null = null;
      setBox(el, origin);
      el.classList.add("is-dragging");
      const paint = () => {
        cancel = null;
        el.style.transform = `translate(${latest.x - origin.x}px, ${latest.y - origin.y}px)`;
        el.style.width = `${latest.w}px`;
        el.style.height = `${latest.h}px`;
      };
      const onMove = (ev: PointerEvent) => {
        latest = computeRect(mode, origin, ev.clientX - sx, ev.clientY - sy);
        cancel ??= nextFrame(paint);
      };
      const onUp = (ev: PointerEvent) => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        cancel?.();
        const final = computeRect(mode, origin, ev.clientX - sx, ev.clientY - sy);
        el.style.transform = "";
        el.classList.remove("is-dragging");
        setBox(el, final);
        // Commit the final box to localStorage so it survives reloads.
        setRect(final);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    };
  }

  /** Phone sheet handle: drag to resize/close, snap on release (a tap toggles via onClick). */
  function startSheetDrag(e: ReactPointerEvent) {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const el = panelRef.current;
    if (!el) return;
    const vh = window.innerHeight;
    const startH =
      el.getBoundingClientRect().height || vh * (snap === "full" ? SNAP_FULL : SNAP_HALF);
    const sy = e.clientY;
    let dy = 0;
    let moved = false;
    let cancel: (() => void) | null = null;
    const paint = () => {
      cancel = null;
      if (dy >= 0) {
        el.style.height = "";
        el.style.transform = `translateY(${dy}px)`;
      } else {
        el.style.transform = "";
        el.style.height = `${Math.min(startH - dy, vh * SNAP_FULL)}px`;
      }
    };
    const onMove = (ev: PointerEvent) => {
      dy = ev.clientY - sy;
      if (!moved && Math.abs(dy) > TAP_SLOP) {
        moved = true;
        el.classList.add("is-dragging");
      }
      if (moved) cancel ??= nextFrame(paint);
    };
    const onUp = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      cancel?.();
      el.style.transform = "";
      el.style.height = "";
      el.classList.remove("is-dragging");
      if (!moved) return;
      suppressClickRef.current = true;
      setTimeout(() => {
        suppressClickRef.current = false;
      }, 0);
      const h = startH - (ev.clientY - sy);
      if (h < vh * SHEET_CLOSE) hide(true);
      else setSnap(h > (vh * (SNAP_HALF + SNAP_FULL)) / 2 ? "full" : "half");
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }

  async function send(text: string, attachments: ChatAttachment[]): Promise<boolean> {
    if (busy || resolvingSend) return false;
    let sessionId = leadSession;
    if (!sessionId) {
      setResolvingSend(true);
      sessionId = await resolveLead();
      setResolvingSend(false);
    }
    // Sending with no session id would start a *new* conversation — a lead
    // chat that is only this browser's. Better to say so than to fork it.
    if (!sessionId) {
      setRefused(true);
      return false;
    }
    setRefused(false);
    const result = await controller.send(sessionId, text, attachments);
    return result.ok;
  }

  const chatsHref = leadSession ? `/chat?session=${encodeURIComponent(leadSession)}` : "/chat";

  return (
    <div data-component="LeadChatHost">
      {mounted ? (
        <div
          ref={panelRef}
          data-component="LeadChatPanel"
          data-layout={desktop ? "panel" : "sheet"}
          data-snap={desktop ? undefined : snap}
          className={desktop ? "leadchat-panel" : "leadchat-panel leadchat-sheet"}
          role="dialog"
          aria-label="Lead chat"
          tabIndex={-1}
          data-state={open ? "open" : "closed"}
          aria-hidden={open ? undefined : true}
          inert={!open}
          style={
            desktop && rect
              ? {
                  left: rect.x,
                  top: rect.y,
                  right: "auto",
                  bottom: "auto",
                  width: rect.w,
                  height: rect.h,
                }
              : undefined
          }
        >
          {desktop ? null : (
            <button
              type="button"
              className="leadchat-grab"
              aria-label={snap === "full" ? "Shrink lead chat" : "Expand lead chat"}
              aria-expanded={snap === "full"}
              title="Tap or drag to resize"
              onPointerDown={startSheetDrag}
              onClick={() => {
                if (suppressClickRef.current) return;
                setSnap((cur) => (cur === "full" ? "half" : "full"));
              }}
            >
              <span aria-hidden className="leadchat-grab-bar" />
            </button>
          )}
          <div className="leadchat-head">
            <div
              className={`min-w-0 flex-1 ${desktop ? "leadchat-drag" : ""}`}
              onPointerDown={desktop ? startPointer("move") : undefined}
              title={desktop ? "Drag to move" : undefined}
            >
              <h3 className="flex items-center gap-1.5 text-sm font-semibold">
                <span aria-hidden className="text-[var(--color-accent)]">✦</span>
                Lead chat
              </h3>
              <p className="truncate text-[11px] text-[var(--color-muted)]">
                Same conversation on every device
              </p>
            </div>
            <Link
              href={chatsHref}
              title="Open the lead conversation in the Chats page"
              className="leadchat-headbtn shrink-0 px-2 text-xs"
            >
              <span aria-hidden>⤢</span> Open in Chats
            </Link>
            <button
              type="button"
              onClick={() => hide(true)}
              aria-label={desktop ? "Minimise" : "Close"}
              title={desktop ? "Minimise" : "Close"}
              className="leadchat-headbtn w-8 shrink-0"
            >
              <span aria-hidden>{desktop ? "—" : "✕"}</span>
            </button>
          </div>
          <ChatThread
            controller={controller}
            sessionId={leadSession}
            density="compact"
            className="overscroll-contain rounded-xl px-1 py-1"
            empty={
              <p className="text-xs text-[var(--color-muted)]">
                {leadSession || resolveFailed
                  ? "The lead session starts with your first message and keeps running from there."
                  : "Loading the conversation…"}
              </p>
            }
          />
          {refused ? (
            <p role="alert" className="mt-2 px-1 text-xs text-red-300">
              {REFUSED_TEXT}
            </p>
          ) : null}
          <Composer
            docked
            sending={busy || resolvingSend}
            stopping={stopping}
            storageEnabled={storageEnabled}
            sessionId={leadSession}
            onSend={send}
            onStop={() => void controller.stop(key)}
          />
          {desktop ? (
            <>
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
            </>
          ) : null}
        </div>
      ) : null}
      <button
        ref={fabRef}
        type="button"
        onClick={show}
        aria-expanded={open}
        aria-label={unread ? "Open lead chat — new reply" : "Open lead chat"}
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
        {unread ? <span aria-hidden data-component="LeadChatUnread" className="leadchat-unread" /> : null}
      </button>
    </div>
  );
}
