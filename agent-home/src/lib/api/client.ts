/**
 * Typed client for the Python AI layer (`/api/*`, `/auth/*`) — FG-20 Wave A2.
 *
 * This is the *only* channel `agent-home` uses for anything agent- or
 * authority-related (one-brain chat, CDP webview, GTS authority writes,
 * onboarding readiness, Core manifest, tool enable/promote, comms). It never
 * re-implements that logic — it forwards to the Python API and replays the
 * bridged Hermes session token so the call is authenticated exactly as the
 * dashboard's own requests are (the gate reads the `hermes_session_at` cookie;
 * see `hermes_cli/dashboard_auth/cookies.py`).
 *
 * Server-only: the browser never calls the Python API directly (BFF pattern),
 * so this module holds the upstream token and runs on the `agent-home` server.
 */
import "server-only";

import { hermesApiBaseUrl } from "@/lib/env";
import type {
  AgentAttachmentPayload,
  ChangeOpResponse,
  ChangesResponse,
  ChatMessagesResponse,
  ChatSendResponse,
  CoreManifestResponse,
  FileAsset,
  FileAssetsResponse,
  FileLinkResponse,
  FileSurfacesResponse,
  GtsGraphResponse,
  IncomingDetail,
  IncomingItem,
  IncomingsFacets,
  IncomingsResponse,
  MemberCreateResponse,
  MemberOkResponse,
  MemberRoleResponse,
  MembersResponse,
  MemoryDocumentsResponse,
  MemoryProjection,
  MemoryQueryPlacement,
  MemoryRowsResponse,
  MemorySummary,
  NotificationAnswerResponse,
  NotificationsResponse,
  OnboardingReadinessResponse,
  Principal,
  Role,
  SessionCreateResponse,
  SessionTag,
  SessionsResponse,
  TagSuggestion,
  StoreMode,
  ToolsResponse,
  TraceDetailResponse,
  TracesResponse,
  WebviewActionKind,
  WebviewActionResponse,
  WebviewMode,
  WebviewSessionResponse,
} from "@/types";

/** Raised when the Python API returns a non-2xx status. */
export class HermesApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "HermesApiError";
  }
}

export interface HermesApiClientOptions {
  /** The bridged upstream Hermes access token to replay (from the session). */
  hermesToken?: string;
  /** Override the base URL (tests / non-default topology). */
  baseUrl?: string;
}

/**
 * A thin, typed `fetch` wrapper around the Python API. Construct one per
 * request from the bridged session token; methods return parsed JSON typed to
 * the shared entity shapes.
 */
export class HermesApiClient {
  private readonly baseUrl: string;
  private readonly hermesToken?: string;

  constructor(opts: HermesApiClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? hermesApiBaseUrl()).replace(/\/+$/, "");
    this.hermesToken = opts.hermesToken;
  }

  /** Low-level request. Prefer the typed methods below where they exist. */
  async request<T>(
    path: string,
    init: RequestInit & { json?: unknown } = {},
  ): Promise<T> {
    const { json, headers, ...rest } = init;
    const finalHeaders = new Headers(headers);
    if (this.hermesToken) {
      // Replay the bridged session both as the dashboard cookie the gate reads
      // and as a bearer header, so either verification path accepts it.
      finalHeaders.set("cookie", `hermes_session_at=${this.hermesToken}`);
      finalHeaders.set("authorization", `Bearer ${this.hermesToken}`);
    }
    if (json !== undefined) {
      finalHeaders.set("content-type", "application/json");
    }
    const res = await fetch(`${this.baseUrl}${path}`, {
      ...rest,
      headers: finalHeaders,
      body: json !== undefined ? JSON.stringify(json) : rest.body,
      // Server-to-server on the same box: never cache authority responses.
      cache: "no-store",
    });
    const text = await res.text();
    const parsed = text ? safeJson(text) : undefined;
    if (!res.ok) {
      throw new HermesApiError(
        res.status,
        `Hermes API ${path} → ${res.status}`,
        parsed ?? text,
      );
    }
    return parsed as T;
  }

  /** Resolve the C1 principal + role for the current bridged session. */
  async whoami(): Promise<{ configured: boolean; principal: Principal | null }> {
    return this.request("/api/comms/whoami");
  }

  /** List the interactive auth providers (login-page bootstrap). Unauthed. */
  async authProviders(): Promise<{
    providers: { name: string; display_name: string; supports_password: boolean }[];
  }> {
    return this.request("/api/auth/providers");
  }

  /**
   * The FG-18 GTS Centre graph (C9) scoped to the principal (C2 + item_grants
   * RLS, enforced server-side in the Python layer). Read-only: creation and
   * scoring stay on the CLI/agent authority paths, so there is no write here.
   */
  async gtsGraph(): Promise<GtsGraphResponse> {
    return this.request("/api/gts/graph");
  }

  /**
   * The FG-14 C7 Core-boundary projection (read-only): active manifest globs,
   * boundary health, and the tail of the Core-denial audit log. Core is
   * immutable to the runtime agent, so this only reflects the boundary.
   */
  async coreManifest(limit = 50): Promise<CoreManifestResponse> {
    return this.request(`/api/core/manifest?limit=${encodeURIComponent(limit)}`);
  }

  /**
   * The C2-scoped list of C8 interaction traces (read-only). Scoping is
   * enforced upstream by the Python ledger; the browser never sees traces the
   * principal may not.
   */
  async traces(limit = 50): Promise<TracesResponse> {
    return this.request(`/api/comms/traces?limit=${encodeURIComponent(limit)}`);
  }

  /** One trace's C2-scoped interaction timeline + rollup (read-only). */
  async trace(traceId: string): Promise<TraceDetailResponse> {
    return this.request(`/api/comms/traces/${encodeURIComponent(traceId)}`);
  }

  /** The C2-scoped FG-12 change log (read-only in this surface). */
  async changes(): Promise<ChangesResponse> {
    return this.request("/api/comms/changes");
  }

  /**
   * The FG-15 onboarding readiness (read-only): the CLI's setup schema +
   * `ready_for_prod` gate. Reports secret *presence* only, never values.
   */
  async onboardingReadiness(): Promise<OnboardingReadinessResponse> {
    return this.request("/api/onboarding/readiness");
  }

  /**
   * The FG-07 tool registry for a datastore mode (read-only in this surface).
   * Enable/config/promote stay on the operator authority paths.
   */
  async tools(mode?: StoreMode): Promise<ToolsResponse> {
    const qs = mode ? `?mode=${encodeURIComponent(mode)}` : "";
    return this.request(`/api/tools${qs}`);
  }

  /**
   * List the principal's conversations (read path). Defaults to the
   * `agent_home` source ordered by most-recent activity so the mobile chat
   * list surfaces the conversations started from this app first.
   */
  async sessions(
    opts: {
      source?: string;
      limit?: number;
      order?: "created" | "recent";
      archived?: "exclude" | "only" | "include";
      tags?: string;
      excludeTags?: string;
      tagMatch?: string;
    } = {},
  ): Promise<SessionsResponse> {
    const params = new URLSearchParams();
    if (opts.source) params.set("source", opts.source);
    params.set("limit", String(opts.limit ?? 30));
    params.set("order", opts.order ?? "recent");
    if (opts.archived) params.set("archived", opts.archived);
    if (opts.tags) params.set("tags", opts.tags);
    if (opts.excludeTags) params.set("exclude_tags", opts.excludeTags);
    if (opts.tagMatch) params.set("tag_match", opts.tagMatch);
    return this.request(`/api/sessions?${params.toString()}`);
  }

  /** Load one conversation's persisted transcript (read path). */
  async sessionMessages(sessionId: string): Promise<ChatMessagesResponse> {
    return this.request(
      `/api/sessions/${encodeURIComponent(sessionId)}/messages`,
    );
  }

  /**
   * Create a new conversation (owner-attributed) via `POST /api/sessions`.
   * Idempotent server-side: a supplied id that already exists is a 409.
   */
  async createSession(sessionId?: string): Promise<SessionCreateResponse> {
    return this.request("/api/sessions", {
      method: "POST",
      json: sessionId ? { session_id: sessionId } : {},
    });
  }

  /**
   * Rename a conversation via `PATCH /api/sessions/{id}` (title only). An empty
   * string clears the title; the backend rejects a duplicate title with 400.
   */
  async renameSession(
    sessionId: string,
    title: string,
  ): Promise<{ ok: boolean; title: string }> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      json: { title },
    });
  }

  /**
   * Archive or unarchive a conversation via `PATCH /api/sessions/{id}`
   * (`archived` only). Archived conversations are hidden from the default list
   * (`archived=exclude`) and returned by `archived=only`.
   */
  async setSessionArchived(
    sessionId: string,
    archived: boolean,
  ): Promise<{ ok: boolean; archived: boolean }> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      json: { archived },
    });
  }

  // ── Session tags ──────────────────────────────────────────────────

  /** List all tags in the workspace with session counts. */
  async listTags(): Promise<{ tags: SessionTag[] }> {
    return this.request(`/api/sessions/tags`);
  }

  /** Create a standalone tag (not attached to any session). */
  async createTag(
    name: string,
    color?: string,
  ): Promise<{ tag: SessionTag }> {
    return this.request(`/api/sessions/tags`, {
      method: "POST",
      json: { name, color: color ?? "blue" },
    });
  }

  /** Get tags attached to a session. */
  async getSessionTags(sessionId: string): Promise<{ tags: SessionTag[] }> {
    return this.request(
      `/api/sessions/${encodeURIComponent(sessionId)}/tags`,
    );
  }

  /** Attach an existing-or-new tag to a session. */
  async addSessionTag(
    sessionId: string,
    name: string,
    color?: string,
  ): Promise<{ tag: SessionTag }> {
    return this.request(`/api/sessions/${encodeURIComponent(sessionId)}/tags`, {
      method: "POST",
      json: { name, color: color ?? "blue" },
    });
  }

  /** Remove a tag from a session. */
  async removeSessionTag(
    sessionId: string,
    tagId: string,
  ): Promise<{ ok: boolean }> {
    return this.request(
      `/api/sessions/${encodeURIComponent(sessionId)}/tags/${encodeURIComponent(tagId)}`,
      { method: "DELETE" },
    );
  }

  /** Delete a tag entirely (removes all session assignments). */
  async deleteTag(tagId: string): Promise<{ ok: boolean }> {
    return this.request(`/api/sessions/tags/${encodeURIComponent(tagId)}`, {
      method: "DELETE",
    });
  }

  /** LLM-suggested tags for a session (awaiting user confirmation). */
  async suggestSessionTags(
    sessionId: string,
  ): Promise<{ suggestions: TagSuggestion[] }> {
    return this.request(
      `/api/sessions/${encodeURIComponent(sessionId)}/tags/suggest`,
      { method: "POST" },
    );
  }

  // ── Cross-session search ─────────────────────────────────────────

  /** FTS5 search across all sessions, returning matching snippets. */
  async searchSessions(
    query: string,
    limit = 20,
  ): Promise<{
    results: Array<{
      session_id: string;
      snippet: string;
      role: string;
      source: string;
      model?: string;
      session_started?: number;
      title?: string | null;
    }>;
  }> {
    const params = new URLSearchParams({ q: query, limit: String(limit) });
    return this.request(`/api/sessions/search?${params.toString()}`);
  }

  /**
   * Send one one-brain turn to a conversation via
   * `POST /api/sessions/{id}/chat` and return the assistant reply. The turn is
   * driven by the shared `AIAgent` + `SessionDB` under the C1 principal — this
   * client never re-implements the conversation loop, it forwards the message.
   */
  async sendChat(
    sessionId: string,
    message: string,
    attachments?: AgentAttachmentPayload[],
  ): Promise<ChatSendResponse> {
    return this.request(
      `/api/sessions/${encodeURIComponent(sessionId)}/chat`,
      {
        method: "POST",
        json:
          attachments && attachments.length > 0
            ? { message, attachments }
            : { message },
      },
    );
  }

  /**
   * Open a streaming turn via `POST /api/sessions/{id}/chat/stream` and return
   * the raw SSE `Response` for the BFF to proxy to the browser. Unlike
   * `request()`, this does NOT consume the body — the caller pipes it through.
   * Tool-approval prompts (approvals.tools) arrive mid-stream as
   * `approval.request` events carrying the run_id; resolve them with
   * `resolveRunApproval`. This is what gives agent-home chat an approval
   * surface, so gated tools (e.g. calendar) prompt instead of failing closed.
   */
  async openChatStream(
    sessionId: string,
    message: string,
    attachments?: AgentAttachmentPayload[],
  ): Promise<Response> {
    const headers = new Headers({ "content-type": "application/json" });
    if (this.hermesToken) {
      headers.set("cookie", `hermes_session_at=${this.hermesToken}`);
      headers.set("authorization", `Bearer ${this.hermesToken}`);
    }
    const body =
      attachments && attachments.length > 0
        ? { message, attachments }
        : { message };
    const res = await fetch(
      `${this.baseUrl}/api/sessions/${encodeURIComponent(sessionId)}/chat/stream`,
      {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        cache: "no-store",
      },
    );
    if (!res.ok || !res.body) {
      const text = res.body ? await res.text().catch(() => "") : "";
      throw new HermesApiError(
        res.status,
        `Hermes API chat/stream → ${res.status}`,
        text,
      );
    }
    return res;
  }

  /**
   * Resolve a pending tool approval for a streamed run. `choice` is one of
   * `once | session | always | deny` (or `approve`, aliased server-side).
   * Forwards to `POST /v1/runs/{run_id}/approval` → `resolve_gateway_approval`,
   * which unblocks the waiting agent turn. This client never decides consent.
   */
  async resolveRunApproval(
    runId: string,
    choice: string,
  ): Promise<{ object: string; run_id: string; choice: string; resolved: number }> {
    return this.request(`/v1/runs/${encodeURIComponent(runId)}/approval`, {
      method: "POST",
      json: { choice },
    });
  }

  /**
   * The caller's open FG-17b webview session (C6 consent-gated), or the
   * default-deny empty state (`session: null`) when none is open. Read path.
   */
  async getWebviewSession(): Promise<WebviewSessionResponse> {
    return this.request("/api/webview/session");
  }

  /**
   * Opt in: open a webview session with an explicit consent scope (allowed
   * domains + read-only/interactive). Attributed to the owner principal
   * (never spoofed). Default-deny means nothing runs until this is called.
   */
  async openWebviewSession(scope: {
    allowed_domains: string[];
    mode: WebviewMode;
  }): Promise<WebviewSessionResponse> {
    return this.request("/api/webview/session", {
      method: "POST",
      json: scope,
    });
  }

  /** Close (opt out of) the caller's webview session. */
  async closeWebviewSession(): Promise<{ ok: boolean; closed: boolean }> {
    return this.request("/api/webview/session", { method: "DELETE" });
  }

  /**
   * Request one agent action against the live page. The Option-B policy
   * (enforced server-side) either allows it (runs via CDP + C8 trace) or
   * escalates it to a per-action C6 approval — this client never decides.
   */
  async requestWebviewAction(action: {
    kind: WebviewActionKind;
    url?: string | null;
    credentialed?: boolean;
    destructive?: boolean;
  }): Promise<WebviewActionResponse> {
    return this.request("/api/webview/action", {
      method: "POST",
      json: action,
    });
  }

  /** Grant or deny a queued per-action C6 approval; on grant the action runs. */
  async resolveWebviewApproval(
    approvalId: string,
    grant: boolean,
  ): Promise<WebviewActionResponse> {
    return this.request(
      `/api/webview/approval/${encodeURIComponent(approvalId)}`,
      { method: "POST", json: { grant } },
    );
  }

  /** List pending comms/notifications visible to the principal (C2-scoped). */
  async notifications(): Promise<NotificationsResponse> {
    return this.request("/api/comms/notifications");
  }

  /**
   * Settle a pending FG-10 item (approval grant/deny, or ask acknowledge). The
   * answer is idempotent across surfaces; `newly_answered` is false if another
   * surface (e.g. Telegram) settled it first. Write path (principal, no `?as=`).
   */
  async answerNotification(
    notificationId: string,
    answer: string,
  ): Promise<NotificationAnswerResponse> {
    return this.request(
      `/api/comms/notifications/${encodeURIComponent(notificationId)}/answer`,
      { method: "POST", json: { answer } },
    );
  }

  /** Undo a visible, reversible FG-12 change (C2 + D6 enforced upstream). */
  async undoChange(changeRef: string): Promise<ChangeOpResponse> {
    return this.request(
      `/api/comms/changes/${encodeURIComponent(changeRef)}/undo`,
      { method: "POST" },
    );
  }

  /** Redo a previously-undone, visible FG-12 change (C2 enforced upstream). */
  async redoChange(changeRef: string): Promise<ChangeOpResponse> {
    return this.request(
      `/api/comms/changes/${encodeURIComponent(changeRef)}/redo`,
      { method: "POST" },
    );
  }

  // --- Member management (PR-4 e-frontend, owner/admin only) --------------
  // The Python layer is the authority: it independently enforces the
  // owner/admin guard and drives GoTrue + the principal store. These methods
  // just forward; the service-role key never leaves the box.

  /** List enrolled members joined with GoTrue account state (owner/admin). */
  async members(): Promise<MembersResponse> {
    return this.request("/api/comms/members");
  }

  /** Create a Supabase account + enrol it as a principal (owner/admin). */
  async createMember(input: {
    email: string;
    password: string;
    display?: string;
    role?: Role;
  }): Promise<MemberCreateResponse> {
    return this.request("/api/comms/members", { method: "POST", json: input });
  }

  /** Change a member's role (never the owner; never to owner). */
  async setMemberRole(userId: string, role: Role): Promise<MemberRoleResponse> {
    return this.request(
      `/api/comms/members/${encodeURIComponent(userId)}/role`,
      { method: "PUT", json: { role } },
    );
  }

  /** Reset a member's temporary password (owner/admin). */
  async setMemberPassword(
    userId: string,
    password: string,
  ): Promise<MemberOkResponse> {
    return this.request(
      `/api/comms/members/${encodeURIComponent(userId)}/password`,
      { method: "POST", json: { password } },
    );
  }

  /** Deactivate (ban) a member's login without deleting the account. */
  async deactivateMember(userId: string): Promise<MemberOkResponse> {
    return this.request(
      `/api/comms/members/${encodeURIComponent(userId)}/deactivate`,
      { method: "POST" },
    );
  }

  /** Reactivate (unban) a previously-deactivated member's login. */
  async activateMember(userId: string): Promise<MemberOkResponse> {
    return this.request(
      `/api/comms/members/${encodeURIComponent(userId)}/activate`,
      { method: "POST" },
    );
  }

  // --- FG-23 memory explorer (read-only) -----------------------------------
  // Unlike `tools(mode)`, these deliberately do NOT forward
  // AGENT_HOME_DATASTORE_MODE — the Python layer resolves the memory tier's
  // own mode (FG-23 D3). Sending `prod` on the current box would report zero
  // memories from an empty `app_prod` schema.

  /** The C2-scoped memory summary: counts, embedding-space health, recall use. */
  async memorySummary(): Promise<MemorySummary> {
    return this.request("/api/memory/explorer/summary");
  }

  /** Paginated + semantic-search rows. `limit: 25` on a phone (not 50). */
  async memoryRows(
    opts: {
      q?: string;
      topic?: string;
      kind?: string;
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<MemoryRowsResponse> {
    const p = new URLSearchParams();
    if (opts.q) p.set("q", opts.q);
    if (opts.topic) p.set("topic", opts.topic);
    if (opts.kind) p.set("kind", opts.kind);
    p.set("limit", String(opts.limit ?? 25));
    p.set("offset", String(opts.offset ?? 0));
    return this.request(`/api/memory/explorer/rows?${p.toString()}`);
  }

  /** The fitted 2-D projection map (scope-filtered, deterministically sampled). */
  async memoryProjection(limit?: number): Promise<MemoryProjection> {
    const qs =
      limit != null ? `?limit=${encodeURIComponent(limit)}` : "";
    return this.request(`/api/memory/explorer/projection${qs}`);
  }

  /** Place a typed query on the map. The text is never persisted upstream. */
  async memoryQuery(text: string): Promise<MemoryQueryPlacement> {
    return this.request("/api/memory/explorer/projection/query", {
      method: "POST",
      json: { text },
    });
  }

  /** The C2-scoped RAG documents list (empty until ingestion runs). */
  async memoryDocuments(): Promise<MemoryDocumentsResponse> {
    return this.request("/api/memory/explorer/documents");
  }

  /** A page of the inbound file registry (arrivals, not memories). */
  async files(
    opts: {
      q?: string;
      surface?: string;
      remembered?: boolean;
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<FileAssetsResponse> {
    const p = new URLSearchParams();
    if (opts.q) p.set("q", opts.q);
    if (opts.surface) p.set("surface", opts.surface);
    if (opts.remembered != null) p.set("remembered", String(opts.remembered));
    p.set("limit", String(opts.limit ?? 50));
    p.set("offset", String(opts.offset ?? 0));
    return this.request(`/api/registry/files?${p.toString()}`);
  }

  /** The surfaces the caller actually has files from, with counts. */
  async fileSurfaces(): Promise<FileSurfacesResponse> {
    return this.request("/api/registry/files/surfaces");
  }

  /** One registered file. 404s when it is absent *or* not visible. */
  async file(id: string): Promise<FileAsset> {
    return this.request(`/api/registry/files/${encodeURIComponent(id)}`);
  }

  /** A short-lived signed link to the bytes, minted after the access check. */
  async fileLink(id: string, download = false): Promise<FileLinkResponse> {
    const qs = download ? "?download=true" : "";
    return this.request(
      `/api/registry/files/${encodeURIComponent(id)}/link${qs}`,
    );
  }

  /**
   * Record a file agent-home has already written to Storage.
   *
   * agent-home owns the bucket credentials for its own uploads, so it uploads
   * the bytes and posts only where they landed — shipping them twice would
   * double the cost of every attachment.
   */
  async registerFile(payload: {
    filename: string;
    content_type: string;
    byte_size: number;
    sha256: string;
    storage_bucket: string;
    storage_path: string;
    conversation?: string;
    surface?: string;
  }): Promise<FileAsset> {
    return this.request("/api/registry/files/register", {
      method: "POST",
      json: { surface: "agent_home", ...payload },
    });
  }

  /** A keyset page of the unified inbox. */
  async incomings(
    opts: {
      q?: string;
      surface?: string;
      kind?: string;
      sender?: string;
      importance?: string;
      tag?: string;
      tag_match?: string;
      exclude_tag?: string;
      remembered?: boolean;
      has_attachments?: boolean;
      since?: string;
      until?: string;
      limit?: number;
      cursor?: string;
    } = {},
  ): Promise<IncomingsResponse> {
    const p = new URLSearchParams();
    for (const key of [
      "q", "surface", "kind", "sender", "importance", "tag", "tag_match",
      "exclude_tag", "since", "until", "cursor",
    ] as const) {
      const value = opts[key];
      if (value) p.set(key, String(value));
    }
    if (opts.remembered != null) p.set("remembered", String(opts.remembered));
    if (opts.has_attachments != null) {
      p.set("has_attachments", String(opts.has_attachments));
    }
    p.set("limit", String(opts.limit ?? 50));
    return this.request(`/api/registry/incomings?${p.toString()}`);
  }

  /** Surfaces, importance levels and tags the caller actually has. */
  async incomingsFacets(): Promise<IncomingsFacets> {
    return this.request("/api/registry/incomings/facets");
  }

  /** One arrival with its attachments and tags. 404s when not visible. */
  async incoming(id: string): Promise<IncomingDetail> {
    return this.request(`/api/registry/incomings/${encodeURIComponent(id)}`);
  }

  /** Attach a tag from the shared vocabulary, creating it when new. */
  async tagIncoming(
    id: string,
    name: string,
    color?: string,
  ): Promise<SessionTag> {
    return this.request(
      `/api/registry/incomings/${encodeURIComponent(id)}/tags`,
      { method: "POST", json: { name, color } },
    );
  }

  /** Detach a tag. The vocabulary keeps the tag itself. */
  async untagIncoming(id: string, tagId: string): Promise<{ removed: boolean }> {
    return this.request(
      `/api/registry/incomings/${encodeURIComponent(id)}/tags/${encodeURIComponent(tagId)}`,
      { method: "DELETE" },
    );
  }

  /** Ingest an arrival into the memory tier and link it back. */
  async rememberIncoming(id: string): Promise<IncomingItem> {
    return this.request(
      `/api/registry/incomings/${encodeURIComponent(id)}/remember`,
      { method: "POST", json: {} },
    );
  }
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
