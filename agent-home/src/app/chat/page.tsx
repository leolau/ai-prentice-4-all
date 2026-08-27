import { ChatHeaderActions } from "@/components/chat/ChatHeaderActions";
import { ChatPane } from "@/components/chat/ChatPane";
import { MobileShell } from "@/components/MobileShell";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import { storageConfigured } from "@/lib/env";
import type { ChatMessage, ProfileSummary, SessionSummary } from "@/types";

/** The profile the chat addresses when the URL names none. */
const DEFAULT_PROFILE = "default";

// Reads the live principal (cookie) + the C2-scoped conversation list per
// request — never at build time.
export const dynamic = "force-dynamic";

/**
 * FG-20 Wave C1 — the one-brain chat tab. BFF: the server resolves the
 * principal and loads the principal's conversations (all sources except
 * cron, and the most recent one's transcript) from the Python API, then
 * hands them to the interactive {@link ChatPane}. Sending routes back
 * through `/api/chat/*` to the principal-scoped
 * `POST /api/sessions/{id}/chat` endpoint.
 */
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ session?: string; profile?: string }>;
}) {
  await requirePrincipal();
  // `?session=<id>` is where a memory's citation link lands. `?profile=<name>`
  // is which profile the chat addresses (FG-28) — a whole HERMES_HOME, so it
  // selects the brain that answers *and* the conversations shown.
  const { session: requested, profile: requestedProfile } = await searchParams;
  const profile = (requestedProfile ?? "").trim() || DEFAULT_PROFILE;

  let sessions: SessionSummary[] = [];
  let sessionId: string | null = null;
  let messages: ChatMessage[] = [];
  let profiles: ProfileSummary[] = [];
  let error: string | null = null;
  try {
    const client = await apiClientForRequest({ profile });
    // A profile that no longer exists must not silently answer as the default:
    // the list is what the picker offers, and an unknown name 404s upstream.
    profiles = (await client.profiles().catch(() => ({ profiles: [] }))).profiles;
    const list = await client.sessions({
      excludeSources: "cron",
      order: "recent",
      // The picker offers every conversation, not a 30-row window.
      limit: 200,
    });
    sessions = list.sessions;
    if (requested && !sessions.some((s) => s.id === requested)) {
      // A memory can cite a cron conversation or one past the first page:
      // fetch wider and prepend it rather than silently opening another.
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
    <MobileShell title="Chat" actions={error ? null : <ChatHeaderActions />}>
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
          profiles={profiles}
          profile={profile}
        />
      )}
    </MobileShell>
  );
}
