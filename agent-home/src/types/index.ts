/**
 * Shared TypeScript types for the `agent-home` seam (FG-20 Wave A2).
 *
 * These are the minimal, extendable shapes the Wave-B/C feature panels
 * consume. They intentionally mirror the Python-side records
 * (`hermes_cli/access.py`, `gts.py`, `interactions.py`) so the BFF and the
 * feature panels speak the same vocabulary as the AI layer + Supabase.
 *
 * They are deliberately *minimal*: only the fields Wave A needs to prove the
 * seam plus the core identifiers later waves will extend. Add fields as each
 * feature panel lands rather than front-loading a speculative surface.
 */

/** The four C1 roles (mirror of `access.Role`). */
export type Role = "owner" | "admin" | "member" | "viewer";

/**
 * The resolved C1 principal a request acts under (mirror of
 * `access.Principal`). This is what the auth bridge establishes and what the
 * server-side Supabase context binds into the `hermes.principal_*` GUCs.
 */
export interface Principal {
  user_id: string;
  display: string;
  role: Role;
  channels: string[];
  is_owner: boolean;
}

/** C3 datastore mode — selects the `app_dev` / `app_prod` schema. */
export type StoreMode = "dev" | "prod";

/** Visibility tag on every scoped row: `shared` or `private:<user_id>`. */
export type Visibility = "shared" | `private:${string}`;

/**
 * How a node's progress is observed/scored (mirror of
 * `gts` evaluation-method dict). The score itself is always engine-computed;
 * this is the user-owned observe/measure definition, never the score.
 */
export interface GtsObservation {
  source: string;
  prompt: string;
  ref?: Record<string, unknown>;
}

export interface GtsEvaluationMethod {
  set_by_user_id: string | null;
  locked: boolean;
  measurable: boolean;
  observation: GtsObservation | null;
  scoring_prompt: string;
}

/**
 * A FG-19 per-item grant attached to a node: the single `assignee` plus any
 * read-only `watcher`s. A grant only confers access while `pending`/`accepted`.
 */
export interface GtsItemGrant {
  id: string;
  item_kind: string;
  item_id: string;
  user_id: string;
  grant: "assignee" | "watcher" | string;
  granted_by: string;
  status: string;
}

/** A GTS goal node (mirror of `gts.GtsGoal.as_dict` + graph enrichment). */
export interface GtsGoal {
  id: string;
  owner_user_id: string;
  visibility: string;
  title: string;
  priority: string;
  status: string;
  level: string;
  parent_goal_id: string | null;
  score: number | null;
  assignee_user_id: string | null;
  evaluation_method: GtsEvaluationMethod;
  grants: GtsItemGrant[];
}

/** A GTS task node (mirror of `gts.GtsTask.as_dict` + graph enrichment). */
export interface GtsTask {
  id: string;
  owner_user_id: string;
  visibility: string;
  title: string;
  priority: string;
  status: string;
  current_state: string;
  parent_task_id: string | null;
  score: number | null;
  assignee_user_id: string | null;
  evaluation_method: GtsEvaluationMethod;
  grants: GtsItemGrant[];
}

/** A GTS skill node (mirror of `gts` skill dict). */
export interface GtsSkill {
  id: string;
  owner_user_id: string;
  visibility: string;
  name: string;
  skill_ref: string;
}

/** Either kind of GTS graph node. */
export type GtsNode =
  | ({ kind: "goal" } & GtsGoal)
  | ({ kind: "task" } & GtsTask);

/**
 * The full C2-scoped GTS graph the Python API returns from `/api/gts/graph`:
 * goal→task→skill hierarchy with the M:N edges, engine-computed scores, and
 * FG-19 assignment. `configured: false` when the app datastore is unset.
 */
export interface GtsGraphResponse {
  configured: boolean;
  principal?: string | null;
  mode?: string;
  goals: GtsGoal[];
  tasks: GtsTask[];
  skills: GtsSkill[];
  task_goals: { task_id: string; goal_id: string }[];
  task_skills: { task_id: string; skill_id: string }[];
  assignment: { enabled: boolean; scheme: string };
}

/** The C8 interaction/trace kinds (mirror of `interactions.InteractionKind`). */
export type InteractionKind =
  | "inbound"
  | "turn"
  | "tool_call"
  | "tool_result"
  | "outbound"
  | "approval"
  | "change"
  | "cost"
  | "error"
  | "core_denied";

/** A single C8 interaction-trace row (mirror of `interactions.Interaction`). */
export interface TraceRow {
  id: string;
  trace_id: string;
  parent_id: string | null;
  ts: string;
  actor_user_id: string;
  session_key: string;
  platform: string;
  kind: InteractionKind;
  ref: string;
  summary: string;
  payload_ref: string | null;
  mode: string;
}

/**
 * A C8 trace summary row (mirror of `interactions.TraceSummary.as_dict`): one
 * conversation/trace rolled up to its span, event count, and per-kind counts.
 */
export interface TraceSummary {
  trace_id: string;
  first_ts: string;
  last_ts: string;
  actor_user_id: string | null;
  session_key: string | null;
  platform: string | null;
  mode: string;
  event_count: number;
  kind_counts: Record<string, number>;
  rolled_up: boolean;
}

/** The C2-scoped list of C8 traces from `/api/comms/traces`. */
export interface TracesResponse {
  configured: boolean;
  principal?: string | null;
  traces: TraceSummary[];
}

/**
 * One trace's timeline projection from `/api/comms/traces/{id}`: the ordered
 * interaction events plus the rolled-up summary (null while still live).
 */
export interface TraceDetailResponse {
  configured: boolean;
  principal?: string | null;
  trace_id: string;
  interactions: TraceRow[];
  rollup: TraceSummary | null;
}

/**
 * A FG-12 change-log row (mirror of the `/api/comms/changes` payload): an
 * agent/human mutation the principal may review, and whether it's reversible.
 */
export interface Change {
  id: string;
  actor_user_id: string | null;
  mode: string;
  target_kind: string;
  reversible: boolean;
  visibility: string;
  undone: boolean;
}

/** The C2-scoped FG-12 change log from `/api/comms/changes`. */
export interface ChangesResponse {
  configured: boolean;
  principal?: string | null;
  changes: Change[];
}

/**
 * A durable Core-write denial (mirror of a `core_audit_log` line): an agent
 * write refused at the C7 boundary. Surfaced by the Core-area view.
 */
export interface CoreDenial {
  id: string;
  ts: number;
  actor_user_id: string;
  mode: string;
  summary: string;
  op?: { kind?: string; op?: string; path?: string; matched_glob?: string };
}

/**
 * The FG-14 C7 Core-boundary projection from `/api/core/manifest` (read-only):
 * the active `core_manifest.yaml` globs, boundary health (`fallback_active`
 * means it's running on the baked-in fail-closed set), and a tail of the
 * durable Core-denial audit log. Core is immutable to the runtime agent.
 */
export interface CoreManifestResponse {
  core_root: string;
  manifest_path: string;
  manifest_present: boolean;
  manifest_parseable: boolean;
  fallback_active: boolean;
  self_protected: boolean;
  globs: string[];
  glob_count: number;
  audit_log_path: string;
  denials: CoreDenial[];
}

/**
 * A single FG-15 onboarding-readiness check (mirror of the CLI setup schema):
 * whether a required/optional prerequisite is `met`, why it matters, and the
 * `hermes …` fix command. Reports secret *presence* only, never values.
 */
export interface OnboardingItem {
  key: string;
  label: string;
  required: boolean;
  rationale: string;
  fix_command: string;
  contract: string;
  met: boolean;
  detail: string;
}

/**
 * The FG-15 onboarding readiness from `/api/onboarding/readiness`: the overall
 * score + `ready_for_prod` gate the CLI computes, plus the per-item checks.
 */
export interface OnboardingReadinessResponse {
  score: number;
  score_pct: number;
  ready_for_prod: boolean;
  required_total: number;
  required_met: number;
  optional_total: number;
  optional_met: number;
  optional_coverage: number;
  missing_required: string[];
  items: OnboardingItem[];
}

/** A registry tool's provenance kind (mirror of the Python tools registry). */
export type ToolKind = "in_house" | "remote" | "builtin";
/** A registry tool's enable status. */
export type ToolStatus = "enabled" | "disabled";

/**
 * A FG-07 tool-registry entry (mirror of `tools_registry.Tool.as_dict`): a
 * tool the operator may enable/promote. This surface is read-only in
 * `agent-home` — enable/config/promote stay on the operator authority paths.
 */
export interface Tool {
  id: string;
  name: string;
  kind: ToolKind;
  stack: string;
  owner_user_id: string;
  visibility: string;
  mode: StoreMode;
  status: ToolStatus;
  enabled: boolean;
  mcp_endpoint_ref: string | null;
  web_url: string | null;
  config_json: Record<string, unknown>;
}

/** The C2-scoped tool registry from `/api/tools` for a datastore mode. */
export interface ToolsResponse {
  configured: boolean;
  mode: StoreMode;
  tools: Tool[];
  detail?: string;
}

/** A chat message role in a one-brain conversation (mirror of the store). */
export type ChatRole = "user" | "assistant" | "system" | "tool";

/**
 * A single persisted conversation message (mirror of the `messages` row the
 * Python `SessionDB` returns). Only the fields the mobile chat pane renders are
 * typed; `tool` rows are kept out of the visible thread.
 */
export interface ChatMessage {
  id?: number;
  role: ChatRole;
  content: string;
  timestamp?: number | string | null;
}

/**
 * A conversation summary row (mirror of `SessionDB.list_sessions_rich`): the
 * id, its human title/preview, message count, and last-active timestamp used to
 * order the mobile conversation list.
 */
/** A tag attached to a session (mirror of `session_tags` table). */
export interface SessionTag {
  id: string;
  name: string;
  color: string;
  session_count?: number;
}

/** An LLM-produced tag suggestion awaiting user confirmation. */
export interface TagSuggestion {
  tag_name: string;
  is_new: boolean;
  reason?: string;
  confidence?: number;
}

export interface SessionSummary {
  id: string;
  source: string;
  title: string | null;
  preview: string | null;
  message_count: number;
  started_at: number | null;
  last_active: number | null;
  ended_at: number | null;
  is_active?: boolean;
  archived?: boolean;
  /** Persisted token totals from the sessions table (used by context-window UI). */
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  reasoning_tokens?: number;
  /** Tags assigned to this session (populated by tag endpoints). */
  tags?: SessionTag[];
}

/** The list of conversations from `GET /api/sessions`. */
export interface SessionsResponse {
  sessions: SessionSummary[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * One profile this box serves, from `GET /api/profiles` (FG-28). A profile is
 * an independent `HERMES_HOME` — its own SOUL, goal, skills, memory, sessions
 * and credentials — so selecting one changes which brain answers a turn.
 */
export interface ProfileSummary {
  name: string;
  is_default: boolean;
  description: string;
}

export interface ProfilesResponse {
  profiles: ProfileSummary[];
}

/**
 * FG-28 — one profile the acting admin may switch the console to.
 *
 * The list is re-derived server-side per request: only profiles where the
 * caller holds an active `admin`/`owner` row in that profile's own
 * `principals` table appear. `administered_profiles` never reads a shared
 * authority store, so there is nothing for the client to keep in sync.
 *
 * `health` is a live probe (`probe_registry_health`): `ok` means the
 * profile's schema is contactable and claimed by itself; `claimed-by-other`
 * would fail closed on connect — the switcher badges it before the user
 * takes a turn there; `unreachable`/`unknown` degrade gracefully.
 */
export interface AdministeredProfileEntry {
  name: string;
  is_default: boolean;
  served: boolean;
  base_url: string;
  schema: string;
  health: "ok" | "core-only" | "unreachable" | "unclaimed" | "claimed-by-other" | "unknown";
  health_detail: string;
}

export interface AdministeredProfilesResponse {
  profiles: AdministeredProfileEntry[];
}

/**
 * FG-30 — a profile suggestion: a proposal (with evidence) that work in one
 * profile has clustered into a distinct sub-goal deserving its own profile.
 *
 * `evidence` is the JSONB the suggestion was raised on, returned verbatim
 * by the Python API. Per §4.2 T3 Q1 the aux-LLM prompt no longer carries the
 * roster, but the stored blob still does; a renderer must not surface it raw
 * (see the suggestion queue screen, which renders the prompt slice).
 */
export interface ProfileSuggestion {
  id: string;
  proposed_name: string;
  proposed_role: string;
  proposed_goal: string;
  parent_goal_id: string | null;
  rationale: string;
  evidence: Record<string, unknown>;
  dedup_key: string;
  origin_profile: string;
  status: "proposed" | "adopted" | "dismissed";
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string | null;
}

/**
 * A reviewed suggestion as the trail renders it. Deliberately narrower than
 * `ProfileSuggestion`: no `evidence`, because that blob carries the
 * `participants` roster (§4.2 T3) and the trail shows status, role and goal.
 */
export interface ProfileSuggestionSummary {
  id: string;
  proposed_name: string;
  proposed_role: string;
  proposed_goal: string;
  status: "proposed" | "adopted" | "dismissed";
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string | null;
}

export interface ProfileSuggestionsResponse {
  /** The open card — at most one (§1.1), with its evidence. */
  suggestions: ProfileSuggestion[];
  /** A capped trail of decisions already made, without evidence. */
  reviewed: ProfileSuggestionSummary[];
}

/** Owner-only adoption returns the new profile's path and goal (§3). */
export interface ProfileSuggestionAdoptResponse {
  ok: boolean;
  name: string;
  path: string;
  goal: string;
}

export interface ProfileSuggestionDismissResponse {
  ok: boolean;
  name: string;
}

/** One conversation's persisted transcript from `GET /api/sessions/{id}/messages`. */
export interface ChatMessagesResponse {
  session_id: string;
  messages: ChatMessage[];
}

/** Token accounting a one-brain turn reports (mirror of the agent usage dict). */
export interface ChatUsage {
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
}

/**
 * The reply from `POST /api/sessions/{id}/chat`: the (possibly resumed) session
 * id the turn landed on, the assistant message, and optional usage.
 */
export interface ChatSendResponse {
  session_id: string;
  message: ChatMessage;
  usage?: ChatUsage;
}

/**
 * A pending tool-approval prompt surfaced mid-turn on the chat stream (the
 * `approval.request` event from `POST /api/sessions/{id}/chat/stream`). A tool
 * matched by `approvals.tools` blocks the agent turn until the user resolves
 * this via `POST /api/chat/approval` with one of `choices`. `command` is
 * already secret-redacted server-side.
 */
export interface ChatApprovalRequest {
  runId: string;
  toolName?: string;
  command?: string;
  description?: string;
  patternKey?: string;
  choices: string[];
}

/** The result of creating a conversation via `POST /api/sessions`. */
export interface SessionCreateResponse {
  session_id: string;
  source: string;
}

/**
 * A media attachment uploaded server-side to principal-scoped Supabase Storage
 * (browser never holds the storage key). The bucket is **private**, so only the
 * durable object `path` is carried: reads are re-signed on demand through the
 * BFF media route after a server-side ownership check (PR-5). There is
 * deliberately no `url` field — a stored URL would either be public or expired.
 */
export interface ChatAttachment {
  path: string;
  name: string;
  content_type: string;
  size: number;
}

/**
 * The per-attachment payload the BFF forwards to the Python chat endpoints so
 * the brain can actually read an upload. The transcript keeps only the durable
 * object `path` ({@link ChatAttachment}); this adds a short-lived signed `url`
 * the server uses once to download the bytes into the shared document cache.
 * It is never persisted or sent to the browser.
 */
export interface AgentAttachmentPayload {
  name: string;
  content_type: string;
  size: number;
  url: string;
}

/** Response of `GET /api/chat/media?path=…` — a short-lived signed read URL. */
export interface ChatMediaUrlResponse {
  path: string;
  url: string;
  expires_in: number;
}

/** Whether a comms item is a consent **approval** (grant/deny) or a proactive **ask** (acknowledge). */
export type NotificationKind = "approval" | "ask";

/** A comms item's settlement state (mirror of `human_comms.NotificationStatus`). */
export type NotificationStatus = "pending" | "answered" | "expired";

/**
 * A FG-10 human-comms item (mirror of `human_comms.Notification.as_dict`): a
 * pending approval or proactive ask visible to the principal (C2-scoped),
 * de-duplicated across surfaces (web/Telegram).
 */
export interface Notification {
  id: string;
  kind: NotificationKind;
  owner_user_id: string;
  visibility: Visibility;
  title: string;
  body: string;
  command: string;
  reversible: boolean;
  status: NotificationStatus;
  answer: string | null;
  answered_by: string | null;
  answered_via: string | null;
  delivered: boolean;
  created_at: string | null;
  answered_at: string | null;
}

/** The C2-scoped FG-10 notifications inbox from `/api/comms/notifications`. */
export interface NotificationsResponse {
  configured: boolean;
  principal?: string | null;
  notifications: Notification[];
}

/**
 * `POST /api/comms/notifications/{id}/answer`: the settled item plus whether
 * *this* surface was the one that settled it (`newly_answered` is false when
 * another surface, e.g. Telegram, answered first).
 */
export interface NotificationAnswerResponse {
  ok: boolean;
  newly_answered: boolean;
  notification: Notification;
}

/** `POST /api/comms/changes/{ref}/{undo|redo}`: the reverted/reapplied change. */
export interface ChangeOpResponse {
  ok: boolean;
  change_ref: string;
  target_kind: string;
}

/**
 * Where an invitation stands (mirror of `invitations.Invitation.status`). The
 * raw token is deliberately absent from this shape — it exists exactly once, in
 * the create/regenerate response, and is stored only as a hash.
 */
export type InvitationStatus = "open" | "used" | "revoked" | "expired";

/** An activation/recovery link's lifecycle (mirror of `Invitation.as_dict`). */
export interface Invitation {
  id: string;
  user_id: string;
  kind: "activation" | "recovery";
  status: InvitationStatus;
  expires_at: string;
  used_at: string | null;
  revoked_at: string | null;
  created_by: string;
  created_at: string;
}

/**
 * A user in this profile (mirror of `members.MemberView.as_dict`): an enrolled
 * principal joined with its Supabase (GoTrue) account state.
 *
 * The two "active" notions are genuinely different and both matter in the UI:
 * `active` is the **box-wide account** (false while banned — which is how a
 * created-but-not-yet-activated user looks), while `enrolled` is this
 * **profile's** enrolment (`principals.active`). Somebody can be a live account
 * elsewhere on the box and a suspended enrolment here.
 */
export interface Member {
  user_id: string;
  display: string;
  role: Role;
  email: string;
  active: boolean;
  enrolled: boolean;
  channels: string[];
  is_owner: boolean;
  invitation: Invitation | null;
}

/** `GET /api/comms/members`: one page of the roster (owner/admin only). */
export interface MembersResponse {
  configured: boolean;
  members: Member[];
  total: number;
  limit: number;
  offset: number;
  /** The profile this console administers (FG-27 derives its schema). */
  profile?: string;
}

/** What any enrolled principal may see about a colleague (FG-26 §3.1). */
export interface DirectoryEntry {
  user_id: string;
  display: string;
  role: Role;
  channels: string[];
}

/** `GET /api/comms/directory`: the colleague list, visible to everyone here. */
export interface DirectoryResponse {
  configured: boolean;
  entries: DirectoryEntry[];
  total: number;
  profile?: string;
}

/**
 * `POST /api/comms/members`: a new account (with its one-time activation link)
 * or an enrolment of an account that already existed on the box.
 *
 * `activation_path` is present only for a genuinely new account and only in
 * this response — somebody who already had an account already has a password,
 * and a link that could be re-fetched would not be single-use.
 */
export interface MemberCreateResponse {
  ok: boolean;
  enrolled_existing: boolean;
  member: { user_id: string; display: string; role: Role; email: string };
  invitation: Invitation | null;
  activation_path: string | null;
}

/** `POST /api/comms/members/{id}/invitation`: a freshly minted link, once. */
export interface MemberInvitationResponse {
  ok: boolean;
  invitation: Invitation;
  activation_path: string;
}

/** One CSV row's outcome (mirror of `members.ImportRow.as_dict`). */
export interface MemberImportRow {
  line: number;
  email: string;
  display: string;
  role: Role;
  planned: string;
  user_id: string;
  error: string;
  activation_path: string | null;
}

/** `POST /api/comms/members/import`: a preview, or what was applied. */
export interface MemberImportResponse {
  ok: boolean;
  dry_run: boolean;
  rows: MemberImportRow[];
  total: number;
  failed: number;
}

/** `PUT /api/comms/members/{id}/role`: the re-roled member. */
export interface MemberRoleResponse {
  ok: boolean;
  member: { user_id: string; role: Role };
}

/** `PUT /api/comms/members/{id}/display`: the renamed member. */
export interface MemberDisplayResponse {
  ok: boolean;
  member: { user_id: string; display: string };
}

/** `POST /api/comms/members/{id}/channels`: the member's linked handles. */
export interface MemberChannelsResponse {
  ok: boolean;
  member: { user_id: string; channels: string[] };
}

/** `DELETE /api/comms/members/{id}`: what happened to the rows they owned. */
export interface MemberDeleteResponse {
  ok: boolean;
  user_id: string;
  strategy: "transfer" | "purge";
  rows_transferred: number;
  rows_deleted: number;
}

/** One identity-administration event from the C5 log (never a raw token). */
export interface IdentityEvent {
  change_ref: string;
  actor_user_id: string;
  action: string;
  user_id: string;
  payload: Record<string, unknown>;
}

/** `GET /api/comms/members/activity`: recent identity events (owner/admin). */
export interface IdentityActivityResponse {
  configured: boolean;
  events: IdentityEvent[];
}

/** Generic ack for the (de)activation member mutations. */
export interface MemberOkResponse {
  ok: boolean;
  active?: boolean;
}

/**
 * FG-17b agent-webview consent grant (mirror of `webview.WebviewScope`): the
 * domains the agent may act on and whether interactive actions are allowed.
 */
export type WebviewMode = "read_only" | "interactive";

export interface WebviewScope {
  allowed_domains: string[];
  mode: WebviewMode;
}

/**
 * An agent action verb requested against the live page (mirror of
 * `webview.ActionKind`). Read-only kinds run autonomously in scope; interactive
 * kinds need an `interactive` grant; `submit`/`download` always escalate.
 */
export type WebviewActionKind =
  | "navigate"
  | "read"
  | "screenshot"
  | "scroll"
  | "click"
  | "type"
  | "select"
  | "submit"
  | "download";

/** The Option-B policy decision for one webview action (mirror of `webview.Decision`). */
export type WebviewDecision = "allow" | "escalate" | "deny";

/**
 * A queued per-action C6 approval (mirror of `webview.PendingApproval.as_dict`):
 * an escalated action awaiting the user's grant/deny.
 */
export interface WebviewPendingApproval {
  id: string;
  kind: WebviewActionKind;
  url: string | null;
  credentialed: boolean;
  destructive: boolean;
  reason: string;
  created_at: number;
  resolved: boolean | null;
}

/**
 * One user's opted-in webview session (mirror of `webview.WebviewSession.as_dict`):
 * the consent scope, the C8 trace id grouping its actions, and any pending
 * approvals. Ephemeral + per-principal (the C2 isolation boundary).
 */
export interface WebviewSession {
  id: string;
  owner_user_id: string;
  scope: WebviewScope;
  profile_dir: string;
  created_at: number;
  trace_id: string;
  pending: WebviewPendingApproval[];
}

/**
 * `GET/POST /api/webview/session`: the caller's open session (or `null` for the
 * default-deny empty state). `configured: false` when the datastore is unset.
 */
export interface WebviewSessionResponse {
  configured: boolean;
  principal?: string | null;
  session: WebviewSession | null;
}

/**
 * The result of requesting/resolving a webview action
 * (`POST /api/webview/action` + `/approval/{id}`): the policy decision, its
 * reason, the CDP execution detail (when it ran), and the escalated approval
 * (when it queued).
 */
export interface WebviewActionResponse {
  decision: WebviewDecision;
  reason: string;
  executed?: boolean;
  detail?: string;
  granted?: boolean;
  approval?: WebviewPendingApproval;
}

// ---------------------------------------------------------------------------
// FG-23 — Memory explorer (read-only). Mirrors `hermes_cli/memory_explorer.py`
// exactly so the BFF, the Python API and the dashboard surface speak the same
// vocabulary. These shapes are lifted from `web/src/screens/MemoryPage.tsx`
// without field-name "improvements" — the API is the contract.
// ---------------------------------------------------------------------------

/** Embedding-space health (mirror of `PgvectorMemoryStore.describe_space()`). */
export interface MemorySpace {
  column_dim: number | null;
  rows_by_model: Record<string, number>;
  configured_model: string;
  healthy: boolean;
}

/** The `/api/memory/explorer/summary` response (C2-scoped counts + recall use). */
export interface MemorySummary {
  space: MemorySpace;
  totals: { memories: number; documents: number; chunks: number };
  by_owner: Record<string, number>;
  by_topic: Record<string, number>;
  by_kind: Record<string, number>;
  growth: { day: string; count: number }[];
  recall_use: {
    never_used: number;
    used_7d: number;
    top: { id: string; text: string; truncated: boolean; uses: number;
           last_used: string | null }[];
  };
}

/** A single memory row from `/api/memory/explorer/rows`. */
export interface MemoryRow {
  id: string; owner_user_id: string; visibility: string; kind: string;
  topic: string | null; text: string; truncated: boolean;
  created_at: string | null; uses: number; last_used: string | null;
  elevated: boolean; provenance: string; score: number | null;
  // Citation fields — where this row came from. `source_session` is set on
  // memories written during a chat; the document fields are set on
  // `kind === "chunk"` rows, which come from an ingested file or Drive doc.
  source_session?: string | null;
  document_id?: string | null;
  document_title?: string | null;
  section?: string | null;
  source_kind?: string | null;
  source_ref?: string | null;
  ordinal?: number;
  file_asset_id?: string | null;  // set when this chunk's document was ingested from a registered file
}

/** Paginated rows response. */
export interface MemoryRowsResponse {
  rows: MemoryRow[]; total: number; limit: number; offset: number;
}

/** One point on the fitted 2-D map. */
export interface MemoryProjectionPoint {
  id: string; x: number; y: number; owner_user_id: string;
  topic: string | null; kind: string; elevated: boolean;
  provenance: string; label: string;
  // Same citation fields as `MemoryRow`, so a clicked dot can say where it
  // came from without a second fetch.
  source_session?: string | null;
  document_id?: string | null;
  document_title?: string | null;
  section?: string | null;
  source_kind?: string | null;
  source_ref?: string | null;
  file_asset_id?: string | null;  // set when this point's chunk was ingested from a registered file
}

/** The `/api/memory/explorer/projection` response. */
export interface MemoryProjection {
  algorithm: string | null;        // "pca" | "umap" | null when never fitted
  computed_at: string | null;
  stale: boolean;                  // model mismatch OR unprojected rows
  unprojected_count: number;
  points: MemoryProjectionPoint[];
  sampled?: boolean;               // true when the point set was deterministically downsampled
  total_points?: number;           // the unsampled count, present when sampled
}

/** The `/api/memory/explorer/projection/query` response. */
export interface MemoryQueryPlacement {
  x: number | null; y: number | null;                    // null ⇒ no position
  nearest: { id: string; score: number }[];
  degraded?: boolean;                                    // UMAP basis unloadable
}

/** A RAG document from `/api/memory/explorer/documents`. */
export interface MemoryDocument {
  id: string; owner_user_id: string; visibility: string; source_kind: string;
  source_ref: string; title: string; chunk_count: number;
  ingested_at: string | null;
  file_asset_id?: string | null;  // set when this document was ingested from a registered file
}

/** Paginated documents response. */
export interface MemoryDocumentsResponse {
  documents: MemoryDocument[]; total: number;
}

/**
 * A registered inbound file from `/api/registry/files` — one arrival, with the
 * provenance the receiving surface knew and nothing downstream does.
 *
 * `remembered` is the deliberate split from memory: a file is stored on
 * arrival, but only appears in the RAG corpus once somebody decided it matters
 * (`document_id` set, `remembered_by` naming the user or the triage skill).
 */
export interface FileAsset {
  id: string;
  owner_user_id: string;
  visibility: string;
  surface: string;                 // agent_home | telegram | whatsapp | email | calendar | …
  account_id: string | null;       // the receiving inbox/bot identity
  conversation: string | null;     // chat/thread/event this arrived in
  sender_id: string | null;
  sender_name: string | null;
  message_id: string | null;
  received_at: string | null;
  filename: string;
  content_type: string;
  byte_size: number;
  sha256: string;
  storage_path: string;            // an object key, never a path on the box
  document_id: string | null;
  remembered_at: string | null;
  remembered_by: string | null;
  remembered: boolean;
  /** The inbound item this file arrived with (for the /todos back-link). */
  inbound_item_id?: string | null;
}

/** Paginated registry listing. */
export interface FileAssetsResponse {
  files: FileAsset[];
  total: number;
  limit: number;
  offset: number;
}

/** Surfaces the caller actually has files from, for the filter chips. */
export interface FileSurfacesResponse {
  surfaces: { surface: string; count: number }[];
}

/** One arrival in the unified inbox: a WhatsApp message, an email, a meeting. */
export interface IncomingItem {
  id: string;
  owner_user_id: string;
  visibility: string;
  surface: string;                 // whatsapp | email | calendar | telegram | …
  account_id: string | null;       // the receiving inbox/number/calendar
  external_id: string;             // the channel's own id, stable across re-polls
  kind: string;                    // message | event | …
  conversation: string | null;
  conversation_name: string | null;
  sender_id: string | null;
  sender_name: string | null;
  subject: string | null;
  body: string;
  occurred_at: string | null;
  ends_at: string | null;          // calendar arrivals only
  registered_at: string | null;
  importance: string | null;
  has_attachments: boolean;
  metadata: Record<string, unknown>;
  document_id: string | null;
  remembered_at: string | null;
  remembered_by: string | null;
  remembered: boolean;
  tags?: SessionTag[];
}

/** A file that arrived with an item, as returned on the detail route. */
export interface IncomingAttachment {
  id: string;
  filename: string;
  content_type: string;
  byte_size: number;
  document_id: string | null;
  remembered: boolean;
}

/** An item plus what it carried. */
export interface IncomingDetail extends IncomingItem {
  attachments: IncomingAttachment[];
}

/**
 * A keyset page. `next_cursor` is null at the end, and there is deliberately
 * no total: counting the filtered set per page is the scan keyset paging
 * exists to avoid.
 */
export interface IncomingsResponse {
  items: IncomingItem[];
  next_cursor: string | null;
}

/** What the filter chips can offer without leading to an empty list. */
export interface IncomingsFacets {
  surfaces: { value: string; count: number }[];
  importance: { value: string; count: number }[];
  tags: SessionTag[];
}

/**
 * The user-facing lifecycle of a to-do. `staged` is the tier that does *not*
 * notify — captured, visible, silent — which is the whole reason it exists;
 * `open` is what the agent decided is worth an interruption.
 */
export type TodoStage = "staged" | "open" | "working" | "done" | "dismissed";

export type TodoPriority = "critical" | "high" | "normal" | "low";

/**
 * A to-do. Stored as an FG-06 `tasks` row, which is why `status`, `origin`
 * and the three state columns come along: the to-do IS the task, extended,
 * not a copy of it in a second table.
 */
export interface Todo {
  id: string;
  owner_user_id: string;
  visibility: string;
  title: string;
  description: string;
  stage: TodoStage;
  status: string;                  // FG-06 status, kept in lockstep with stage
  priority: TodoPriority;
  origin: string;                  // explicit | discovered | triage
  current_state: string;
  trigger_state: string;
  completion_state: string;
  due_at: string | null;
  source_kind: string | null;      // inbound | analysis | user | agent | cron
  source_ref: string | null;       // the inbound_items row, when it has one
  source_note: string | null;      // provenance when the arrival is unlinked
  notified_at: string | null;
  snoozed_until: string | null;
  closed_at: string | null;
  outcome: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** One recorded move along the lifecycle, from `task_transitions`. */
export interface TodoTransition {
  from: string;                    // e.g. "stage:staged"
  to: string;                      // e.g. "stage:open"
  at: string | null;
  actor: string;                   // "user:leo", "skill:email-triage", …
}

/**
 * A to-do plus why it exists. `source` is the arrival that caused it, when it
 * is still resolvable — "why is this here?" is the first question a user asks
 * of anything an agent put in front of them.
 */
export interface TodoDetail extends Todo {
  history: TodoTransition[];
  source: IncomingItem | null;
  /** The memory document the source arrival produced (absent when not remembered). */
  memory?: { id: string; title: string };
}

/** A keyset page of to-dos. `next_cursor` is null at the end. */
export interface TodosResponse {
  items: Todo[];
  next_cursor: string | null;
}

/** What the to-do filter chips can offer without leading to an empty list. */
export interface TodosFacets {
  stages: { value: string; count: number }[];
  priorities: { value: string; count: number }[];
  source_kinds: { value: string; count: number }[];
}

/**
 * An outgoing action a finished to-do proposes.
 *
 * `channel`, `target` and `account_id` default server-side to the arrival the
 * to-do came from (contract C4: the reply leaves by the account it arrived
 * on), so a client can propose a reply while only drafting the words.
 */
export interface ProposedAction {
  channel?: string;
  target?: string;
  account_id?: string | null;
  thread_id?: string | null;
  subject?: string;
  body: string;
}

/** What the server raised for a proposed action, or why it could not. */
export interface TodoProposal {
  todo_id?: string;
  action?: ProposedAction;
  notification_id?: string;
  /** The command the approval authorises — shown before the user approves. */
  command?: string;
  auto_approved?: boolean;
  error?: string;
}

/**
 * The finished to-do, plus the approval its completion proposed.
 *
 * `proposal` is absent when nothing was proposed, and carries `error` when the
 * work was closed but the draft could not be raised — completing is never lost
 * to a malformed draft.
 */
export interface TodoCompletion extends Todo {
  proposal?: TodoProposal;
}

/** A short-lived signed link to a registered file's bytes. */
export interface FileLinkResponse {
  url: string;
  expires_in: number;
  filename: string;
  content_type: string;
}

/**
 * A goal in the entity's tree (FG-29).
 *
 * `tier` is a *lifetime*, and lifetime decides where the goal is allowed to
 * appear: `entity` and `profile` goals live for quarters and years so they may
 * sit in the cached system prompt, `participant` goals go beside the user
 * profile, and an `operational` goal — which can change mid-session — never
 * reaches a prompt at all.
 */
export interface EntityGoal {
  id: string;
  title: string;
  description: string;
  tier: "entity" | "profile" | "participant" | "operational";
  parent_goal_id: string | null;
  primary_metric: string | null;
  priority: string;
  status: string;
  /** Set on a copy published from another profile; read-only where it landed. */
  published_from_profile: string | null;
  /** True when the source goal changed after this copy was made. */
  stale: boolean;
}

/**
 * The entity goal as settings sees it.
 *
 * `effective` is always `"next_session"`: the goal's text is part of the cached
 * system prompt, so an edit cannot reach a conversation already in flight — the
 * UI says that rather than implying the change is live.
 */
export interface EntityGoalResponse {
  goal: EntityGoal | null;
  created: boolean;
  prompt_tier?: "stable" | "volatile" | "never";
  effective: "next_session";
}

/**
 * FG-31 — capacity headroom. One derived verdict plus the reading behind it.
 *
 * `binding_constraint` is the point: a percentage does not tell the owner what
 * to do, and when `hardware_helps` is false a bigger box cannot move the bound
 * at all (SQLite's single writer), so the UI must not offer an upgrade there.
 */
export interface CapacityBound {
  name: string;
  state: "comfortable" | "watch" | "constrained";
  reason: string;
  hardware_helps: boolean;
}

export interface CapacityIndicators {
  active_conversations: number;
  per_profile: Record<string, number>;
  cap_here: number | null;
  /** Sum of every profile's cap; null when any profile leaves it unbounded. */
  cap_box_wide: number | null;
  available_mb: number | null;
  total_mb: number | null;
  hermes_rss_mb: number | null;
  by_process: Record<string, number>;
  write_lock_waits_per_hour: number | null;
  write_lock_waited_s: number | null;
  turn_p50_s: number | null;
  turn_p95_s: number | null;
  turn_samples: number;
  profile_count: number;
}

export interface CapacityResponse {
  state: "comfortable" | "watch" | "constrained";
  headline: string;
  summary: string;
  binding_constraint: {
    name: string;
    reason: string;
    hardware_helps: boolean;
  } | null;
  bounds: CapacityBound[];
  recommendations: string[];
  indicators: CapacityIndicators;
  /** Indicators that could not be read — shown as unknown, never as zero. */
  unavailable: string[];
  collected_at: number;
}
