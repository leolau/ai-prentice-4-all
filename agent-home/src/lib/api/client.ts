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
  CapacityResponse,
  ChangeOpResponse,
  ChangesResponse,
  ChatMessagesResponse,
  ChatSendResponse,
  CoreManifestResponse,
  DirectoryResponse,
  EntityGoalResponse,
  FileAsset,
  FileAssetsResponse,
  FileLinkResponse,
  FileSurfacesResponse,
  GtsGraphResponse,
  IdentityActivityResponse,
  IncomingDetail,
  IncomingItem,
  IncomingsFacets,
  IncomingsResponse,
  MemberChannelsResponse,
  MemberCreateResponse,
  MemberDeleteResponse,
  MemberDisplayResponse,
  MemberImportResponse,
  MemberInvitationResponse,
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
  ProfilesResponse,
  ProposedAction,
  Role,
  SessionCreateResponse,
  SessionTag,
  SessionsResponse,
  TagSuggestion,
  Todo,
  TodoCompletion,
  TodoDetail,
  TodosFacets,
  TodosResponse,
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
  /**
   * Bind every call to a named Hermes profile (FG-28). A profile *is* a
   * `HERMES_HOME`: its own SOUL, config, memory, skills, credentials and
   * `state.db`. Binding it on the client rather than per call is what keeps a
   * turn and the reads around it (session list, transcript, tags) on the same
   * profile — a per-method flag drifts the moment one call site forgets it.
   * Omitted or `"default"` means the box's own home, unchanged.
   */
  profile?: string;
}

/**
 * A thin, typed `fetch` wrapper around the Python API. Construct one per
 * request from the bridged session token; methods return parsed JSON typed to
 * the shared entity shapes.
 */
export class HermesApiClient {
  private readonly baseUrl: string;
  private readonly hermesToken?: string;
  private readonly profile?: string;

  constructor(opts: HermesApiClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? hermesApiBaseUrl()).replace(/\/+$/, "");
    this.hermesToken = opts.hermesToken;
    const profile = (opts.profile ?? "").trim();
    this.profile = profile && profile !== "default" ? profile : undefined;
  }

  /** The profile this client is bound to, or undefined for the box's own home. */
  boundProfile(): string | undefined {
    return this.profile;
  }

  /**
   * Add the bound profile to a path's query string. The Python API reads
   * `?profile=` on its read endpoints; an endpoint that doesn't declare it
   * ignores the parameter, so this is safe to apply uniformly.
   */
  private scopedPath(path: string): string {
    if (!this.profile) return path;
    const sep = path.includes("?") ? "&" : "?";
    return `${path}${sep}profile=${encodeURIComponent(this.profile)}`;
  }

  /** Add the bound profile to a JSON body (write endpoints read it there). */
  private scopedJson(json: unknown): unknown {
    if (!this.profile || json === undefined) return json;
    if (json === null || typeof json !== "object" || Array.isArray(json)) return json;
    return { profile: this.profile, ...(json as Record<string, unknown>) };
  }

  /** Low-level request. Prefer the typed methods below where they exist. */
  async request<T>(
    path: string,
    init: RequestInit & { json?: unknown } = {},
  ): Promise<T> {
    const { json: rawJson, headers, ...rest } = init;
    const json = this.scopedJson(rawJson);
    path = this.scopedPath(path);
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

  /**
   * List the profiles this box serves (FG-28). Each is an independent
   * `HERMES_HOME` — its own SOUL, goal, skills, memory and credentials — so the
   * chat surface can address one deliberately instead of always the default.
   */
  async profiles(): Promise<ProfilesResponse> {
    return this.request("/api/profiles");
  }

  /**
   * FG-31 capacity headroom — one verdict naming the bound that produced it.
   * Read-only, and box-wide: the active-session registry is profile-local, so
   * Python aggregates every profile's leases rather than reporting this one's.
   */
  async capacity(): Promise<CapacityResponse> {
    return this.request("/api/capacity");
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
    // Streams bypass `request()` (the body is piped, not consumed), so the
    // bound profile is applied here explicitly — a streamed turn must run under
    // the same profile's brain as a non-streamed one.
    const body = this.scopedJson(
      attachments && attachments.length > 0
        ? { message, attachments }
        : { message },
    );
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

  // --- User management (FG-26, owner/admin unless noted) -------------------
  // The Python layer is the authority: it independently enforces the
  // owner/admin guard and drives GoTrue + the principal store. These methods
  // just forward; the service-role key never leaves the box, and **no password
  // crosses this seam in either direction** — a new account is created banned
  // with a server-side random password and activated from an invitation.

  /** One page of this profile's roster, searched/filtered in Postgres. */
  async members(
    opts: {
      limit?: number;
      offset?: number;
      q?: string;
      role?: Role;
      /** `true` = enrolled here, `false` = suspended here, omitted = both. */
      active?: boolean;
    } = {},
  ): Promise<MembersResponse> {
    const query = new URLSearchParams();
    if (opts.limit !== undefined) query.set("limit", String(opts.limit));
    if (opts.offset !== undefined) query.set("offset", String(opts.offset));
    if (opts.q) query.set("q", opts.q);
    if (opts.role) query.set("role", opts.role);
    if (opts.active !== undefined) query.set("active", String(opts.active));
    const suffix = query.toString();
    return this.request(`/api/comms/members${suffix ? `?${suffix}` : ""}`);
  }

  /**
   * The colleague directory — readable by **every enrolled principal**, not
   * just admins. Built from this profile's principals, never from the box-wide
   * account table.
   */
  async directory(
    opts: { limit?: number; offset?: number; q?: string } = {},
  ): Promise<DirectoryResponse> {
    const query = new URLSearchParams();
    if (opts.limit !== undefined) query.set("limit", String(opts.limit));
    if (opts.offset !== undefined) query.set("offset", String(opts.offset));
    if (opts.q) query.set("q", opts.q);
    const suffix = query.toString();
    return this.request(`/api/comms/directory${suffix ? `?${suffix}` : ""}`);
  }

  /**
   * Enrol somebody into `profile` (owner/admin). `profile` is **required** and
   * travels to the server, which refuses a foreign profile with 409 before any
   * account is created (cross-profile assignment is FG-28's).
   */
  async createMember(input: {
    email: string;
    profile: string;
    display?: string;
    role?: Role;
  }): Promise<MemberCreateResponse> {
    return this.request("/api/comms/members", { method: "POST", json: input });
  }

  /** Preview (or apply) a `email,display,role` CSV bulk enrolment. */
  async importMembers(input: {
    csv: string;
    profile: string;
    dry_run: boolean;
  }): Promise<MemberImportResponse> {
    return this.request("/api/comms/members/import", {
      method: "POST",
      json: input,
    });
  }

  /** Mint (or regenerate) a one-time activation link — shown exactly once. */
  async issueMemberInvitation(
    userId: string,
  ): Promise<MemberInvitationResponse> {
    return this.request(
      `/api/comms/members/${encodeURIComponent(userId)}/invitation`,
      { method: "POST" },
    );
  }

  /** Revoke every open invitation for a user (a mis-sent link, killed). */
  async revokeMemberInvitation(
    userId: string,
  ): Promise<{ ok: boolean; revoked: number }> {
    return this.request(
      `/api/comms/members/${encodeURIComponent(userId)}/invitation`,
      { method: "DELETE" },
    );
  }

  /** Rename a member within this profile (owner/admin). */
  async setMemberDisplay(
    userId: string,
    display: string,
  ): Promise<MemberDisplayResponse> {
    return this.request(
      `/api/comms/members/${encodeURIComponent(userId)}/display`,
      { method: "PUT", json: { display } },
    );
  }

  /** Map an inbound channel handle onto an enrolled member (owner/admin). */
  async linkMemberChannel(
    userId: string,
    input: { platform: string; channel_user_id: string },
  ): Promise<MemberChannelsResponse> {
    return this.request(
      `/api/comms/members/${encodeURIComponent(userId)}/channels`,
      { method: "POST", json: input },
    );
  }

  /** Recent identity-administration events from the C5 log (owner/admin). */
  async memberActivity(limit = 50): Promise<IdentityActivityResponse> {
    return this.request(`/api/comms/members/activity?limit=${limit}`);
  }

  /**
   * Remove an enrolment (**owner only**), stating what happens to the rows it
   * owns: `transfer` needs `transferTo`, `purge` deletes the private ones.
   * Nothing cascades to memories, files or GTS items, hence the requirement.
   */
  async deleteMember(
    userId: string,
    opts: { strategy: "transfer" | "purge"; transferTo?: string },
  ): Promise<MemberDeleteResponse> {
    const query = new URLSearchParams({ strategy: opts.strategy });
    if (opts.transferTo) query.set("transfer_to", opts.transferTo);
    return this.request(
      `/api/comms/members/${encodeURIComponent(userId)}?${query.toString()}`,
      { method: "DELETE" },
    );
  }

  /**
   * Redeem an invitation: set the password and lift the ban. **Unauthenticated**
   * upstream (the invitee has no session yet), and every failure mode answers
   * identically so this cannot be used to discover whether a token was real.
   */
  async redeemInvitation(
    input: {
      token: string;
      password: string;
    },
    clientIp = "",
  ): Promise<{ ok: boolean }> {
    return this.request("/api/auth/invitations/redeem", {
      method: "POST",
      json: input,
      headers: forwardedFor(clientIp),
    });
  }

  /** Ask an administrator for a reset link. Always answers `{ok: true}`. */
  async requestInvitation(
    email: string,
    clientIp = "",
  ): Promise<{ ok: boolean }> {
    return this.request("/api/auth/invitations/request", {
      method: "POST",
      json: { email },
      headers: forwardedFor(clientIp),
    });
  }

  /** Change a member's role (never the owner; never to owner). */
  async setMemberRole(userId: string, role: Role): Promise<MemberRoleResponse> {
    return this.request(
      `/api/comms/members/${encodeURIComponent(userId)}/role`,
      { method: "PUT", json: { role } },
    );
  }

  /** Suspend a member's enrolment in this profile (the account survives). */
  async deactivateMember(userId: string): Promise<MemberOkResponse> {
    return this.request(
      `/api/comms/members/${encodeURIComponent(userId)}/deactivate`,
      { method: "POST" },
    );
  }

  /** Restore a suspended enrolment in this profile. */
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

  /** A keyset page of to-dos. Snoozed ones are hidden unless asked for. */
  async todos(
    opts: {
      q?: string;
      stage?: string;
      priority?: string;
      source_kind?: string;
      source_ref?: string;
      due_before?: string;
      include_snoozed?: boolean;
      limit?: number;
      cursor?: string;
    } = {},
  ): Promise<TodosResponse> {
    const p = new URLSearchParams();
    for (const key of [
      "q", "stage", "priority", "source_kind", "source_ref", "due_before",
      "cursor",
    ] as const) {
      const value = opts[key];
      if (value) p.set(key, String(value));
    }
    if (opts.include_snoozed) p.set("include_snoozed", "true");
    p.set("limit", String(opts.limit ?? 50));
    return this.request(`/api/registry/todos?${p.toString()}`);
  }

  /** Stages, priorities and sources the caller actually has. */
  async todosFacets(): Promise<TodosFacets> {
    return this.request("/api/registry/todos/facets");
  }

  /** One to-do with its history and the arrival behind it. */
  async todo(id: string): Promise<TodoDetail> {
    return this.request(`/api/registry/todos/${encodeURIComponent(id)}`);
  }

  /** A to-do the user wrote themselves. Lands `open`, never deduped. */
  async createTodo(payload: {
    title: string;
    description?: string;
    priority?: string;
    due_at?: string | null;
  }): Promise<Todo> {
    return this.request("/api/registry/todos", {
      method: "POST",
      json: payload,
    });
  }

  /** Edit the descriptive fields. Lifecycle moves go through `setTodoStage`. */
  async updateTodo(
    id: string,
    payload: {
      title?: string;
      description?: string;
      priority?: string;
      due_at?: string | null;
    },
  ): Promise<Todo> {
    return this.request(`/api/registry/todos/${encodeURIComponent(id)}`, {
      method: "PATCH",
      json: payload,
    });
  }

  /** Promote, start, finish or dismiss — audited with the acting principal. */
  async setTodoStage(
    id: string,
    stage: string,
    outcome?: string,
  ): Promise<Todo> {
    return this.request(`/api/registry/todos/${encodeURIComponent(id)}/stage`, {
      method: "POST",
      json: { stage, outcome },
    });
  }

  /**
   * Finish a to-do, optionally proposing what should leave because of it.
   *
   * The proposal is never a send: it becomes an irreversible approval the user
   * answers themselves.
   */
  async completeTodo(
    id: string,
    payload: { outcome?: string; proposed_action?: ProposedAction } = {},
  ): Promise<TodoCompletion> {
    return this.request(
      `/api/registry/todos/${encodeURIComponent(id)}/complete`,
      { method: "POST", json: payload },
    );
  }

  /** Hide a to-do until `until`, re-arming its notification for then. */
  async snoozeTodo(id: string, until: string): Promise<Todo> {
    return this.request(`/api/registry/todos/${encodeURIComponent(id)}/snooze`, {
      method: "POST",
      json: { until },
    });
  }

  /** Move a to-do to `working` and optionally spawn a seeded session. */
  async startTodo(
    id: string,
    payload: { session?: boolean } = {},
  ): Promise<Todo & { session_id?: string | null; spawned?: boolean }> {
    return this.request(`/api/registry/todos/${encodeURIComponent(id)}/start`, {
      method: "POST",
      json: payload,
    });
  }

  /** Promote a to-do into a project card (card lands in `triage`). */
  async promoteTodo(
    id: string,
    payload: { project: string },
  ): Promise<Todo & { card_id?: string; project_id?: string }> {
    return this.request(`/api/registry/todos/${encodeURIComponent(id)}/promote`, {
      method: "POST",
      json: payload,
    });
  }

  /**
   * The entity goal — what every sub-goal ladders into.
   *
   * Creates the default first goal for an owner who has none, so settings is
   * never an empty box with no explanation of what belongs in it.
   */
  async entityGoal(): Promise<EntityGoalResponse> {
    return this.request("/api/registry/goals/entity");
  }

  /** Edit the entity goal. Owner only, and effective in the next session. */
  async updateEntityGoal(payload: {
    title?: string;
    description?: string;
  }): Promise<EntityGoalResponse> {
    return this.request("/api/registry/goals/entity", {
      method: "PATCH",
      json: payload,
    });
  }
}

/**
 * `X-Forwarded-For` carrying the invitee's own address, or no header at all.
 *
 * The unauthenticated invitation endpoints throttle per IP, and every one of
 * their requests reaches Python from *this* server — so without this the whole
 * internet shares one bucket. Python only trusts the header from a loopback
 * peer, which this call is.
 */
function forwardedFor(clientIp: string): Record<string, string> {
  return clientIp ? { "X-Forwarded-For": clientIp } : {};
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
