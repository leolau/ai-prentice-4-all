import { ChatPane } from "@/components/chat/ChatPane";
import { MobileShell } from "@/components/MobileShell";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import { storageConfigured } from "@/lib/env";
import type { ChatMessage, SessionSummary } from "@/types";

// Reads the live principal (cookie) + the C2-scoped conversation list per
// request — never at build time.
export const dynamic = "force-dynamic";

/**
 * FG-20 Wave C1 — the one-brain chat tab. BFF: the server resolves the
 * principal and loads the principal's `agent_home` conversations (and the most
 * recent one's transcript) from the Python API, then hands them to the
 * interactive {@link ChatPane}. Sending routes back through `/api/chat/*` to
 * the principal-scoped `POST /api/sessions/{id}/chat` endpoint.
 */
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ session?: string }>;
}) {
  await requirePrincipal();
  // `?session=<id>` is where a memory's citation link lands.
  const { session: requested } = await searchParams;

  let sessions: SessionSummary[] = [];
  let sessionId: string | null = null;
  let messages: ChatMessage[] = [];
  let error: string | null = null;
  try {
    const client = await apiClientForRequest();
    const list = await client.sessions({ source: "agent_home", order: "recent" });
    sessions = list.sessions;
    if (requested && !sessions.some((s) => s.id === requested)) {
      // A memory can be written by any surface (gateway, cron, the CLI), so
      // the linked conversation is often outside the agent_home list: fetch
      // it unfiltered and prepend it rather than silently opening another.
      const all = await client.sessions({ order: "recent", limit: 200 });
      const match = all.sessions.find((s) => s.id === requested);
      if (match) sessions = [match, ...sessions];
    }
    if (requested && sessions.some((s) => s.id === requested)) {
      sessionId = requested;
    } else if (sessions.length > 0) {
      sessionId = sessions[0].id;
    }
    if (sessionId) {
      const transcript = await client.sessionMessages(sessionId);
      messages = transcript.messages;
    }
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load conversations";
  }

  return (
    <MobileShell title="Chat">
      {error ? (
        <div
          data-component="ChatError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load your conversations ({error}).
        </div>
      ) : (
        <ChatPane
          initialSessions={sessions}
          initialSessionId={sessionId}
          initialMessages={messages}
          storageEnabled={storageConfigured()}
        />
      )}
    </MobileShell>
  );
}
