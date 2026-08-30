/**
 * Push-enrollment store for the app channel (server-only).
 *
 * VAPID keys and device subscriptions live in the private Supabase bucket
 * under a `_push/` prefix — the BFF owns them because it is the surface the
 * browser talks to; the Python sender pulls them through
 * `/api/notifications/config`. One global document each: enrollment is rare
 * (a handful of devices), so a single read-modify-write per change is fine.
 */
import "server-only";

import { createClient } from "@supabase/supabase-js";

import {
  mediaBucket,
  storageConfigured,
  supabaseStorageKey,
  supabaseUrl,
} from "@/lib/env";
import { generateVapidKeypair, type VapidDocument } from "@/lib/push/vapid";

export interface PushSubscriptionRecord {
  endpoint: string;
  keys: { p256dh: string; auth: string };
  user_agent?: string | null;
  created_at: string;
}

const VAPID_PATH = "_push/vapid.json";
const SUBS_PATH = "_push/subscriptions.json";

export function pushConfigured(): boolean {
  return storageConfigured();
}

function client() {
  const key = supabaseStorageKey();
  if (!key) {
    throw new Error("agent-home: push store is not configured.");
  }
  return createClient(supabaseUrl(), key, {
    auth: { persistSession: false },
  });
}

async function readJson<T>(path: string): Promise<T | null> {
  const { data, error } = await client().storage
    .from(mediaBucket())
    .download(path);
  if (error || !data) return null;
  try {
    return JSON.parse(await data.text()) as T;
  } catch {
    return null;
  }
}

async function writeJson(path: string, value: unknown): Promise<void> {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  const { error } = await client().storage
    .from(mediaBucket())
    .upload(path, bytes, {
      contentType: "application/json",
      upsert: true,
    });
  if (error) {
    throw new Error(`agent-home: push store write failed — ${error.message}`);
  }
}

export async function getVapid(): Promise<VapidDocument | null> {
  return readJson<VapidDocument>(VAPID_PATH);
}

/** The VAPID keypair — generated and stored on first use. Also regenerates
 * documents whose private key is PEM (pre-DER-fix): py_vapid rejects PEM. */
export async function ensureVapid(): Promise<VapidDocument> {
  const existing = await getVapid();
  if (
    existing?.private_key &&
    existing?.public_key &&
    !existing.private_key.startsWith("-----BEGIN")
  ) {
    return existing;
  }
  const doc = generateVapidKeypair();
  await writeJson(VAPID_PATH, doc);
  return doc;
}

export async function listSubscriptions(): Promise<PushSubscriptionRecord[]> {
  const list = await readJson<PushSubscriptionRecord[]>(SUBS_PATH);
  return Array.isArray(list) ? list : [];
}

/** Enroll or re-enroll a device (endpoint is the identity). */
export async function addSubscription(record: PushSubscriptionRecord): Promise<void> {
  const list = await listSubscriptions();
  const next = [record, ...list.filter((s) => s.endpoint !== record.endpoint)];
  await writeJson(SUBS_PATH, next);
}

/** Unenroll a device. Returns whether anything was removed. */
export async function removeSubscription(endpoint: string): Promise<boolean> {
  const list = await listSubscriptions();
  const next = list.filter((s) => s.endpoint !== endpoint);
  if (next.length === list.length) return false;
  await writeJson(SUBS_PATH, next);
  return true;
}
