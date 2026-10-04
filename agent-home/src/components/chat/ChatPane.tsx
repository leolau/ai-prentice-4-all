"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  useTransition,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { useRouter } from "next/navigation";

import { ArchivedModal } from "@/components/chat/ArchivedModal";
import { Composer } from "@/components/chat/Composer";
import { ConversationList, titleOf } from "@/components/chat/ConversationList";
import { InSessionSearch } from "@/components/chat/InSessionSearch";
import { DEFAULT_PROFILE, ProfilePicker } from "@/components/chat/ProfilePicker";
import { SessionModal } from "@/components/chat/SessionModal";
import { SessionSearchBar } from "@/components/chat/SessionSearchBar";
import { TagFilterBar } from "@/components/chat/TagFilterBar";
import { ChatThread } from "@/components/chat/thread/ChatThread";
import {
  categorizeSession,
  categoryOverrideTagName,
  CHAT_CATEGORY_LABELS,
  isCategoryOverrideTag,
  type ChatCategory,
} from "@/lib/chat/categorize";
import {
  keyOf,
  useBusyKeys,
  useChatController,
  useThread,
  type ChatController,
} from "@/lib/chat/chat-controller";
import { chatHeaderActionsRef } from "@/lib/chat/header-actions";
import { markSessionRead, readLastReadMap, type LastReadMap } from "@/lib/chat/last-read";
import { withProfileBody, withProfileQuery } from "@/lib/chat/profile";
import { CHAT_SESSION_LIST_LIMIT } from "@/lib/chat/session-limits";
import { fetchSessionList } from "@/lib/chat/session-list-fetch";
import { nextActiveAfterArchive } from "@/lib/chat/session-order";
import type {
  ChatAttachment,
  ChatMessage,
  ProfileSummary,
  SessionSummary,
  SessionTag,
  TagSuggestion,
} from "@/types";

export interface ChatPaneProps {
  initialSessions: SessionSummary[];
  initialSessionId: string | null;
  /** First transcript page of `initialSessionId` (server-rendered). */
  initialMessages: ChatMessage[];
  /** Older pages exist beyond `initialMessages`. */
  initialHasMore?: boolean;
  /** Phone only: open on the list or straight on the thread (deep link). */
  initialScreen?: "list" | "thread";
  storageEnabled: boolean;
  /** Every profile this box serves (FG-28); one entry means no picker. */
  profiles: ProfileSummary[];
  /** The profile this pane is showing — its sessions, and its brain. */
  profile: string;
  /** Text the composer opens with (`?draft=`); never sent on its own. */
  initialDraft?: string;
}

type Screen = "list" | "thread";

const DESKTOP_QUERY = "(min-width: 1024px)";
/** `history.state` marker for a thread screen pushed from the list. */
const PUSHED = "chatThreadPushed";

const isDesktop = () =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia(DESKTOP_QUERY).matches;

/** Write `?session=` without a server round trip. */
function writeSessionUrl(id: string | null, mode: "push" | "replace") {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("session", id);
  else url.searchParams.delete("session");
  url.searchParams.delete("draft");
  const href = `${url.pathname}${url.search}${url.hash}`;
  // Fresh state objects: Next's patched history copies its own router state
  // in and syncs useSearchParams (an object carrying `__NA` would skip that).
  if (mode === "push") {
    window.history.pushState({ [PUSHED]: true }, "", href);
  } else {
    window.history.replaceState({ [PUSHED]: wasPushed() }, "", href);
  }
}

const wasPushed = (): boolean =>
  typeof window !== "undefined" &&
  Boolean((window.history.state as Record<string, unknown> | null)?.[PUSHED]);

/** Only the Stop state of the open turn — re-renders on that flag, not per token. */
function useStopping(c: ChatController, key: string): boolean {
  const subscribe = useCallback((cb: () => void) => c.subscribeLive(key, cb), [c, key]);
  const get = useCallback(() => c.getLive(key)?.activity.stopping ?? false, [c, key]);
  return useSyncExternalStore(subscribe, get, get);
}

/**
 * In-conversation search. Pages the whole history in first so matches in
 * older pages are found; indices line up with ChatThread's `data-msg-index`.
 */
function InConversationSearch({
  controller,
  sessionId,
  initialQuery,
  onClose,
  highlightRef,
}: {
  controller: ChatController;
  sessionId: string;
  initialQuery?: string;
  onClose: () => void;
  highlightRef: React.RefObject<((msgIndex: number, term: string) => void) | null>;
}) {
  const thread = useThread(controller, sessionId);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let live = true;
    void controller.loadAll(sessionId).finally(() => {
      if (live) setLoading(false);
    });
    return () => {
      live = false;
    };
  }, [controller, sessionId]);
  return (
    <InSessionSearch
      messages={thread.messages}
      initialQuery={initialQuery}
      loading={loading}
      onClose={onClose}
      highlightRef={highlightRef}
    />
  );
}

const iconButton =
  "flex h-8 min-w-8 shrink-0 items-center justify-center rounded-lg border px-2 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]";

/**
 * FG-20 Wave C1 — the one-brain Chats page on the shared chat engine. Desktop
 * (lg+) is a conversation sidebar beside the open thread; a phone shows the
 * list, then the thread with a back button. Turns run through
 * `ChatController` (`/api/chat/*` BFF), so a conversation keeps streaming
 * while another one is on screen. It never talks to the AI layer directly.
 */
export function ChatPane({
  initialSessions,
  initialSessionId,
  initialMessages,
  initialHasMore = false,
  initialScreen = "list",
  storageEnabled,
  profiles,
  profile,
  initialDraft,
}: ChatPaneProps) {
  const router = useRouter();
  const [switchingProfile, startProfileSwitch] = useTransition();
  // Every client read carries the selected profile: a profile is a whole
  // HERMES_HOME, and reading another one's sessions files replies in the
  // wrong history.
  const path = (p: string) => withProfileQuery(p, profile);
  const payload = <T extends object>(b: T) => withProfileBody(b, profile);

  const [sessions, setSessions] = useState<SessionSummary[]>(initialSessions);
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId);
  const [screen, setScreen] = useState<Screen>(initialScreen);
  const selectedRef = useRef<string | null>(sessionId);
  const [lastRead, setLastRead] = useState<LastReadMap | null>(null);
  const [detailsSession, setDetailsSession] = useState<SessionSummary | null>(null);
  const [archivedOpen, setArchivedOpen] = useState(false);

  // ── Tagging state ──
  const [allTags, setAllTags] = useState<SessionTag[]>([]);
  const [sessionTags, setSessionTags] = useState<SessionTag[]>([]);
  const [tagSuggestions, setTagSuggestions] = useState<TagSuggestion[]>([]);
  const [includeTags, setIncludeTags] = useState<string[]>([]);
  const [excludeTags, setExcludeTags] = useState<string[]>([]);
  const [matchMode, setMatchMode] = useState<"any" | "all">("any");

  // ── Search state ──
  const [crossSearch, setCrossSearch] = useState<string | null>(null);
  const [inSearch, setInSearch] = useState<{ query: string } | null>(null);
  const [highlightTerm, setHighlightTerm] = useState<string | undefined>();
  const highlightCallbackRef = useRef<((msgIndex: number, term: string) => void) | null>(
    (_msgIndex: number, term: string) => setHighlightTerm(term || undefined),
  );

  const controller = useChatController({
    profile,
    adoptLandedSession: true,
    onTurnSettled: (key, landed) => {
      const selected = selectedRef.current;
      if (keyOf(selected) === key || (landed && landed === selected)) {
        markRead(landed ?? selected);
      }
      void refreshSessions();
    },
  });
  // The server-rendered first page, once, before the first paint.
  useState(() => {
    if (initialSessionId) {
      controller.seed(initialSessionId, initialMessages, initialHasMore);
    }
    return null;
  });

  const busyKeys = useBusyKeys(controller);
  const selKey = keyOf(sessionId);
  const selBusy = busyKeys.includes(selKey);
  const stopping = useStopping(controller, selKey);

  function markRead(id: string | null) {
    if (!id) return;
    markSessionRead(id);
    setLastRead(readLastReadMap());
  }

  useEffect(() => {
    selectedRef.current = sessionId;
  }, [sessionId]);

  // Last-read markers are browser-local; read them after hydration. A deep
  // link (or the desktop's open thread) counts as seen.
  useEffect(() => {
    if (initialSessionId && (initialScreen === "thread" || isDesktop())) {
      markSessionRead(initialSessionId);
    }
    setLastRead(readLastReadMap());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Idle watch: a turn can start on the open conversation from another
  // surface (Telegram, another device, a scheduled job) or be mid-flight
  // when the page loads; the controller attaches to it.
  useEffect(() => {
    if (!sessionId) return;
    return controller.watchActive(sessionId);
  }, [controller, sessionId]);

  // Register the header action callbacks (startNew / openArchived) for the
  // ChatHeaderActions in the MobileShell header. Every render, so the ref
  // holds the latest closures; reset to noops on unmount.
  useEffect(() => {
    chatHeaderActionsRef.current.startNew = startNewConversation;
    chatHeaderActionsRef.current.openArchived = () => setArchivedOpen(true);
  });
  useEffect(() => {
    return () => {
      chatHeaderActionsRef.current = {
        startNew: () => {},
        openArchived: () => {},
      };
    };
  }, []);

  // Browser Back from a pushed thread screen returns to the list (phone).
  useEffect(() => {
    const onPop = () => {
      const id = new URL(window.location.href).searchParams.get("session");
      if (!id || !wasPushed()) {
        setScreen("list");
        return;
      }
      setScreen("thread");
      if (id !== selectedRef.current) {
        selectedRef.current = id;
        setSessionId(id);
        void controller.open(id);
      }
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [controller]);

  // ── Layout: fill the viewport below the shell header ─────────────────
  const rootRef = useRef<HTMLDivElement | null>(null);
  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const measure = () => {
      const top = el.getBoundingClientRect().top + window.scrollY;
      el.style.setProperty("--chat-pane-top", `${Math.max(0, Math.round(top))}px`);
    };
    measure();
    window.addEventListener("resize", measure);
    const ro =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(document.body);
    return () => {
      window.removeEventListener("resize", measure);
      ro?.disconnect();
    };
  }, []);

  // ── Navigation ───────────────────────────────────────────────────────

  function showThread(id: string | null) {
    const pushed = !isDesktop() && screen === "list";
    setScreen("thread");
    writeSessionUrl(id, pushed ? "push" : "replace");
  }

  function selectSession(id: string) {
    if (id !== sessionId) {
      markRead(sessionId);
      selectedRef.current = id;
      setSessionId(id);
      setInSearch(null);
      setHighlightTerm(undefined);
    }
    void controller.open(id);
    markRead(id);
    showThread(id);
  }

  function startNewConversation() {
    if (sessionId !== null) markRead(sessionId);
    selectedRef.current = null;
    setSessionId(null);
    setInSearch(null);
    setHighlightTerm(undefined);
    showThread(null);
  }

  function backToList() {
    if (wasPushed()) {
      window.history.back();
      return;
    }
    setScreen("list");
    writeSessionUrl(null, "replace");
  }

  function onThreadKeyDown(e: ReactKeyboardEvent<HTMLElement>) {
    if (e.key !== "Escape" || e.defaultPrevented || isDesktop()) return;
    const tag = (e.target as HTMLElement).tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    e.preventDefault();
    backToList();
  }

  // ── Turns ────────────────────────────────────────────────────────────

  async function send(text: string, attachments: ChatAttachment[]): Promise<boolean> {
    const from = sessionId;
    const res = await controller.send(from, text, attachments);
    // A new conversation lands on its id; follow it if still on screen.
    if (res.sessionId && res.sessionId !== from && selectedRef.current === from) {
      selectedRef.current = res.sessionId;
      setSessionId(res.sessionId);
      markRead(res.sessionId);
      writeSessionUrl(res.sessionId, "replace");
    }
    return res.ok;
  }

  /**
   * Switch which profile this chat addresses. Navigates (the server loads
   * that profile's sessions) and is refused while a turn is streaming.
   */
  function switchProfile(next: string) {
    if (next === profile || busyKeys.length > 0) return;
    startProfileSwitch(() => {
      router.push(
        next === DEFAULT_PROFILE ? "/chat" : `/chat?profile=${encodeURIComponent(next)}`,
      );
    });
  }

  // ── Sessions ─────────────────────────────────────────────────────────

  async function refreshSessions() {
    try {
      const params = new URLSearchParams();
      // Match the first-paint fetch: a refresh must not shrink the list.
      params.set("limit", String(CHAT_SESSION_LIST_LIMIT));
      // Cron sessions are grouped as "Scheduled", not hidden.
      params.set("exclude_sources", "");
      if (includeTags.length > 0) params.set("tags", includeTags.join(","));
      if (excludeTags.length > 0) params.set("exclude_tags", excludeTags.join(","));
      params.set("tag_match", matchMode);
      // `force`: callers run right after a mutation; a shared GET could
      // answer with pre-mutation state.
      const body = await fetchSessionList(path(`/api/chat/sessions?${params.toString()}`), {
        force: true,
      });
      if (body?.sessions) setSessions(body.sessions);
    } catch {
      // A stale conversation list is non-fatal.
    }
  }

  async function renameSession(title: string) {
    const s = detailsSession;
    if (!s) return;
    const res = await fetch("/api/chat/sessions/rename", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload({ sessionId: s.id, title })),
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: string };
      throw new Error(body.detail ?? "The conversation could not be renamed.");
    }
    const body = (await res.json()) as { title?: string };
    const newTitle = body.title ?? title;
    setSessions((prev) =>
      prev.map((x) => (x.id === s.id ? { ...x, title: newTitle || null } : x)),
    );
    void refreshSessions();
  }

  async function setArchived(id: string, archived: boolean) {
    const res = await fetch("/api/chat/sessions/archive", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload({ sessionId: id, archived })),
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: string };
      throw new Error(
        body.detail ??
          (archived
            ? "The conversation could not be archived."
            : "The conversation could not be un-archived."),
      );
    }
  }

  async function archiveSession() {
    const s = detailsSession;
    if (!s) return;
    await setArchived(s.id, true);
    // Archiving the open conversation moves to its neighbour in the list
    // (newest first), not to a blank "New conversation".
    const ordered = [...sessions].sort(
      (a, b) =>
        (b.last_active ?? b.started_at ?? 0) - (a.last_active ?? a.started_at ?? 0),
    );
    const nextId = nextActiveAfterArchive(
      ordered.map((x) => x.id),
      s.id,
    );
    setSessions((prev) => prev.filter((x) => x.id !== s.id));
    if (sessionId === s.id) {
      if (nextId) selectSession(nextId);
      else startNewConversation();
    }
    controller.evict(s.id);
    void refreshSessions();
  }

  async function unarchiveSession(id: string) {
    await setArchived(id, false);
    void refreshSessions();
  }

  // ── Tag handlers ─────────────────────────────────────────────────────

  async function loadAllTags() {
    try {
      const res = await fetch(path("/api/chat/sessions/tags"), { cache: "no-store" });
      if (res.ok) {
        const body = (await res.json()) as { tags?: SessionTag[] };
        if (body.tags) setAllTags(body.tags);
      }
    } catch {
      /* non-fatal */
    }
  }

  async function onOpenDetails(s: SessionSummary) {
    setDetailsSession(s);
    setTagSuggestions([]);
    setSessionTags([]);
    if (s.id) {
      try {
        const res = await fetch(
          path(`/api/chat/sessions/tags/get?sessionId=${encodeURIComponent(s.id)}`),
          { cache: "no-store" },
        );
        if (res.ok) {
          const body = (await res.json()) as { tags?: SessionTag[] };
          if (body.tags) setSessionTags(body.tags);
        }
      } catch {
        /* non-fatal */
      }
    }
  }

  async function addTag(name: string) {
    const s = detailsSession;
    if (!s) return;
    const res = await fetch("/api/chat/sessions/tags/add", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload({ sessionId: s.id, name })),
    });
    if (!res.ok) throw new Error("Failed to add tag.");
    const body = (await res.json()) as { tag?: SessionTag };
    if (body.tag) {
      setSessionTags((prev) =>
        prev.some((t) => t.name === body.tag!.name) ? prev : [...prev, body.tag!],
      );
      void loadAllTags();
    }
  }

  async function removeTag(tagId: string) {
    const s = detailsSession;
    if (!s) return;
    const res = await fetch("/api/chat/sessions/tags/remove", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload({ sessionId: s.id, tagId })),
    });
    if (!res.ok) throw new Error("Failed to remove tag.");
    setSessionTags((prev) => prev.filter((t) => t.id !== tagId));
    void loadAllTags();
  }

  /** Move the conversation to another category: a reserved `category:<value>`
   * tag (see `lib/chat/categorize.ts`), at most one per session. */
  async function setSessionCategory(next: ChatCategory) {
    const existing = sessionTags.find((t) => isCategoryOverrideTag(t));
    if (existing) await removeTag(existing.id);
    await addTag(categoryOverrideTagName(next));
    void refreshSessions();
  }

  async function suggestTags() {
    const s = detailsSession;
    if (!s) return;
    const res = await fetch("/api/chat/sessions/tags/suggest", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload({ sessionId: s.id })),
    });
    if (!res.ok) throw new Error("Failed to suggest tags.");
    const body = (await res.json()) as { suggestions?: TagSuggestion[] };
    setTagSuggestions(body.suggestions ?? []);
  }

  async function acceptSuggestion(suggestion: TagSuggestion) {
    const s = detailsSession;
    if (!s) return;
    const res = await fetch("/api/chat/sessions/tags/add", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload({ sessionId: s.id, name: suggestion.tag_name })),
    });
    if (!res.ok) throw new Error("Failed to add tag.");
    const body = (await res.json()) as { tag?: SessionTag };
    if (body.tag) {
      setSessionTags((prev) =>
        prev.some((t) => t.name === body.tag!.name) ? prev : [...prev, body.tag!],
      );
      void loadAllTags();
    }
    setTagSuggestions((prev) => prev.filter((t) => t !== suggestion));
  }

  async function dismissSuggestion(suggestion: TagSuggestion) {
    setTagSuggestions((prev) => prev.filter((t) => t !== suggestion));
  }

  // Auto-suggest tags when the details open on a session with 5+ messages.
  useEffect(() => {
    if (!detailsSession || detailsSession.message_count < 5 || tagSuggestions.length > 0) {
      return;
    }
    const t = setTimeout(() => void suggestTags(), 0);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detailsSession]);

  // Load all tags on mount.
  useEffect(() => {
    const t = setTimeout(() => void loadAllTags(), 0);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleTagFilter(name: string) {
    if (includeTags.includes(name)) {
      // include → exclude
      setIncludeTags((prev) => prev.filter((n) => n !== name));
      setExcludeTags((prev) => [...prev, name]);
    } else if (excludeTags.includes(name)) {
      // exclude → clear
      setExcludeTags((prev) => prev.filter((n) => n !== name));
    } else {
      // none → include
      setIncludeTags((prev) => [...prev, name]);
    }
  }

  // Re-fetch sessions when the tag filter changes.
  useEffect(() => {
    void refreshSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeTags, excludeTags, matchMode]);

  // ── Search ───────────────────────────────────────────────────────────

  const crossSessionSearch = useCallback(
    async (q: string) => {
      const res = await fetch(
        withProfileQuery(`/api/chat/sessions/search?q=${encodeURIComponent(q)}`, profile),
        { cache: "no-store" },
      );
      if (!res.ok) return [];
      const body = (await res.json()) as {
        results?: Array<{
          session_id: string;
          snippet: string;
          role: string;
          title?: string | null;
        }>;
      };
      return body.results ?? [];
    },
    [profile],
  );

  function jumpToSearchResult(result: { session_id: string; snippet: string }) {
    const term = result.snippet.replace(/>>>/g, "").replace(/<<</g, "").trim().slice(0, 30);
    selectSession(result.session_id);
    setHighlightTerm(term || undefined);
    setInSearch({ query: term });
  }

  // ── Render ───────────────────────────────────────────────────────────

  const selected = useMemo(
    () => (sessionId ? (sessions.find((s) => s.id === sessionId) ?? null) : null),
    [sessions, sessionId],
  );
  const title = sessionId === null ? "New conversation" : selected ? titleOf(selected) : "Conversation";
  const category = selected ? categorizeSession(selected) : null;
  const tags = (selected?.tags ?? []).filter((t) => !isCategoryOverrideTag(t)).slice(0, 3);
  const filtersActive = includeTags.length > 0 || excludeTags.length > 0;

  const chipClass =
    "shrink-0 rounded-full border border-[var(--color-border)] px-2 py-0.5 text-[11px] text-[var(--color-muted)]";

  return (
    <div
      ref={rootRef}
      data-component="ChatPane"
      data-screen={screen}
      // Fills the viewport below the shell header down to the shell's bottom
      // padding, so the composer sits clear of the Coral FABs and the page
      // itself never scrolls; the list / thread scroll inside.
      className="flex min-h-0 flex-col overflow-hidden [--chat-pane-bottom:calc(var(--coral-clearance)+var(--safe-bottom)+1rem)] lg:flex-row lg:gap-4 lg:[--chat-pane-bottom:calc(var(--coral-clearance)+var(--safe-bottom))]"
      style={{
        height:
          "calc(100dvh - var(--chat-pane-top, calc(6rem + var(--safe-top))) - var(--chat-pane-bottom))",
      }}
    >
      <aside
        data-component="ChatSidebar"
        aria-label="Conversation list"
        className={`min-h-0 flex-1 flex-col overflow-hidden lg:flex lg:w-80 lg:flex-none lg:rounded-2xl lg:border lg:border-[var(--color-border)] lg:bg-[var(--color-surface)] ${
          screen === "list" ? "flex" : "hidden"
        }`}
      >
        <ConversationList
          sessions={sessions}
          selectedId={sessionId}
          busyKeys={busyKeys}
          lastRead={lastRead}
          onSelect={selectSession}
          onSelectNew={startNewConversation}
          header={
            <ProfilePicker
              profiles={profiles}
              selected={profile}
              onSelect={switchProfile}
              disabled={busyKeys.length > 0}
              switching={switchingProfile}
            />
          }
          filters={
            allTags.length > 0 ? (
              <TagFilterBar
                tags={allTags}
                includeTags={includeTags}
                excludeTags={excludeTags}
                matchMode={matchMode}
                onToggle={toggleTagFilter}
                onMatchModeChange={setMatchMode}
              />
            ) : null
          }
          filtersActive={filtersActive}
          onSearchMessages={(q) => setCrossSearch(q)}
          searchPanel={
            crossSearch !== null ? (
              <SessionSearchBar
                key={crossSearch}
                initialQuery={crossSearch}
                onSearch={crossSessionSearch}
                onJumpToResult={(result) => jumpToSearchResult(result)}
                onClose={() => setCrossSearch(null)}
              />
            ) : null
          }
        />
      </aside>

      <section
        data-component="ChatThreadPane"
        aria-label={title}
        onKeyDown={onThreadKeyDown}
        className={`min-h-0 flex-1 flex-col overflow-hidden lg:flex lg:rounded-2xl lg:border lg:border-[var(--color-border)] lg:bg-[var(--color-surface)] ${
          screen === "thread" ? "flex" : "hidden"
        }`}
      >
        <header
          data-component="ThreadHeader"
          className="flex shrink-0 items-center gap-2 border-b border-[var(--color-border)] px-1 py-2 lg:px-3"
        >
          <button
            type="button"
            onClick={backToList}
            aria-label="Back to conversations"
            className={`${iconButton} border-transparent text-[var(--color-fg)] lg:hidden`}
          >
            <span aria-hidden="true">‹</span>
          </button>
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-[var(--color-fg)]">
            {title}
          </h2>
          {category ? (
            <span data-component="ThreadCategory" className={chipClass}>
              {CHAT_CATEGORY_LABELS[category]}
            </span>
          ) : null}
          {tags.map((t) => (
            <span key={t.id} className={`${chipClass} hidden sm:inline`}>
              #{t.name}
            </span>
          ))}
          {sessionId ? (
            <button
              type="button"
              aria-pressed={inSearch !== null}
              aria-label="Search in conversation"
              title="Search in conversation"
              onClick={() => {
                setInSearch((v) => (v ? null : { query: "" }));
                setHighlightTerm(undefined);
              }}
              className={`${iconButton} ${
                inSearch
                  ? "border-[var(--color-accent)] text-[var(--color-fg)]"
                  : "border-[var(--color-border)] text-[var(--color-muted)]"
              }`}
            >
              <span aria-hidden="true">⌕</span>
            </button>
          ) : null}
          {selected ? (
            <button
              type="button"
              aria-label="Conversation details"
              title="Details, rename, tags, archive"
              onClick={() => void onOpenDetails(selected)}
              className={`${iconButton} border-[var(--color-border)] text-[var(--color-muted)]`}
            >
              <span aria-hidden="true">⋯</span>
            </button>
          ) : null}
        </header>

        {inSearch && sessionId ? (
          <InConversationSearch
            key={`${sessionId}:${inSearch.query}`}
            controller={controller}
            sessionId={sessionId}
            initialQuery={inSearch.query}
            onClose={() => {
              setInSearch(null);
              setHighlightTerm(undefined);
            }}
            highlightRef={highlightCallbackRef}
          />
        ) : null}

        <ChatThread
          controller={controller}
          sessionId={sessionId}
          highlightTerm={highlightTerm}
          className="px-3 py-3"
          empty={
            <p className="py-8 text-center text-sm text-[var(--color-muted)]">
              {sessionId
                ? "No messages yet — say hello."
                : "Start a new conversation with your agent."}
            </p>
          }
        />

        <div className="shrink-0 px-3 pb-2 lg:pb-3">
          <Composer
            docked
            sending={selBusy}
            stopping={stopping}
            storageEnabled={storageEnabled}
            sessionId={sessionId}
            initialText={initialDraft}
            onSend={send}
            onStop={() => void controller.stop(selKey)}
          />
        </div>
      </section>

      {detailsSession ? (
        <SessionModal
          session={detailsSession}
          onClose={() => {
            setDetailsSession(null);
            setTagSuggestions([]);
            setSessionTags([]);
          }}
          onRename={renameSession}
          onArchive={archiveSession}
          category={categorizeSession({ ...detailsSession, tags: sessionTags })}
          onSetCategory={setSessionCategory}
          tags={sessionTags}
          allTags={allTags}
          tagSuggestions={tagSuggestions}
          onAddTag={addTag}
          onRemoveTag={removeTag}
          onAcceptSuggestion={acceptSuggestion}
          onDismissSuggestion={dismissSuggestion}
        />
      ) : null}

      {archivedOpen ? (
        <ArchivedModal
          onClose={() => setArchivedOpen(false)}
          onUnarchive={unarchiveSession}
          profile={profile}
        />
      ) : null}
    </div>
  );
}
