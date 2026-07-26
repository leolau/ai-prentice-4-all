/**
 * Server-side environment access for `agent-home` (FG-20 Wave A).
 *
 * Env-var policy (per AGENTS.md + FG-20): `agent-home` introduces **zero new
 * non-secret `HERMES_*` env vars**. The only env this app reads is either:
 *   - a real **secret** (DB DSN, Supabase anon key, the session-signing
 *     secret) — these belong in `.env`, exactly like a Supabase key; or
 *   - **deploy topology** for this Node server (the Python API base URL, the
 *     Supabase project URL, the datastore mode), namespaced `AGENT_HOME_*` so
 *     it never collides with or extends the Python `HERMES_*` namespace.
 *
 * All accessors are lazy so `next build` succeeds on a box with nothing
 * configured; a value is only *required* at request time by the helper that
 * needs it, which then fails loudly with an actionable message.
 */
import type { StoreMode } from "@/types";

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `agent-home: missing required environment variable ${name}. ` +
        `See agent-home/.env.example.`,
    );
  }
  return value;
}

/** HMAC secret used to sign the `agent-home` session cookie. Required (secret). */
export function sessionSecret(): string {
  return required("AGENT_HOME_SESSION_SECRET");
}

/**
 * Base URL of the Python AI layer (`/api/*`, `/auth/*`). Deploy topology,
 * defaults to the on-box loopback the current prod Caddy fronts.
 */
export function hermesApiBaseUrl(): string {
  return (process.env.AGENT_HOME_API_URL || "http://127.0.0.1:9119").replace(
    /\/+$/,
    "",
  );
}

/** Postgres DSN for server-side Supabase reads. Required at read time (secret). */
export function databaseUrl(): string {
  // `DATABASE_URL` is the same env name the Python backend points
  // `datastore.supabase_app.dsn` at, but in a multi-user deploy it must NOT be
  // the privileged (BYPASSRLS) DSN: point it at the least-privilege,
  // NOBYPASSRLS **login** serving role (`agent_home_app`, provisioned by
  // `hermes owner read-role`) so this app's direct reads have C2 visibility
  // enforced by Postgres FORCE'd RLS. The privileged DSN stays with Python
  // (migrations, writes, background jobs).
  return required("DATABASE_URL");
}

/** Supabase project URL (for RLS-scoped Realtime). Deploy topology. */
export function supabaseUrl(): string {
  return required("SUPABASE_URL");
}

/** Supabase anon key (browser-safe; RLS enforces access). Secret-ish. */
export function supabaseAnonKey(): string {
  return required("SUPABASE_ANON_KEY");
}

/**
 * Server-side Supabase Storage key for uploading chat media (secret; `.env`).
 * The browser never receives this — uploads go through the `agent-home` BFF.
 * Returns undefined when unset so the attach feature degrades gracefully.
 */
export function supabaseStorageKey(): string | undefined {
  return process.env.SUPABASE_SERVICE_ROLE_KEY || undefined;
}

/** The Storage bucket chat media is written to. Deploy topology. */
export function mediaBucket(): string {
  return process.env.AGENT_HOME_MEDIA_BUCKET || "agent-home-media";
}

/**
 * TTL (seconds) of the signed URLs the media read route mints. The bucket is
 * private, so a leaked URL is only useful for this window — keep it short.
 * Deploy topology (`AGENT_HOME_MEDIA_URL_TTL`), defaults to 60s and is clamped
 * to 5s..1h so a typo can never mint a long-lived URL.
 */
export function mediaSignedUrlTtlSeconds(): number {
  const raw = Number(process.env.AGENT_HOME_MEDIA_URL_TTL);
  if (!Number.isFinite(raw) || raw <= 0) return 60;
  return Math.min(3600, Math.max(5, Math.floor(raw)));
}

/** Whether server-side chat-media uploads are configured on this box. */
export function storageConfigured(): boolean {
  return Boolean(process.env.SUPABASE_URL && process.env.SUPABASE_SERVICE_ROLE_KEY);
}

/**
 * C3 datastore mode. Dashboard/CLI-style surfaces default to `dev`; the prod
 * deploy sets `AGENT_HOME_DATASTORE_MODE=prod`. Never invents a third mode.
 */
export function datastoreMode(): StoreMode {
  const raw = (process.env.AGENT_HOME_DATASTORE_MODE || "dev").trim();
  if (raw === "prod") return "prod";
  if (raw === "dev") return "dev";
  throw new Error(
    `agent-home: invalid AGENT_HOME_DATASTORE_MODE '${raw}'; expected 'dev' or 'prod'.`,
  );
}

/** The Postgres schema for the resolved mode (contract C3). */
export function schemaForMode(mode: StoreMode): "app_dev" | "app_prod" {
  return mode === "prod" ? "app_prod" : "app_dev";
}
