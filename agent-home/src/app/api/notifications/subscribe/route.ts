/**
 * POST / DELETE /api/notifications/subscribe — device enrollment for the
 * app channel's Web Push.
 *
 * POST is user-driven (cookie principal): the browser hands over its
 * PushSubscription. DELETE accepts either the cookie principal (toggle-off
 * in Settings) or the shared app-push secret — the Python sender uses it to
 * drop subscriptions the push service reports gone (404/410).
 */
import { NextResponse } from "next/server";

import { getPrincipal } from "@/lib/auth/principal";
import { verifyAppPushSecret } from "@/lib/push/secret";
import {
  addSubscription,
  pushConfigured,
  removeSubscription,
} from "@/lib/push/store";

const ENDPOINT_LIMIT = 2048;
const KEY_LIMIT = 512;

interface SubscribeBody {
  endpoint?: unknown;
  keys?: { p256dh?: unknown; auth?: unknown };
}

function parseBody(body: SubscribeBody):
  | { endpoint: string; p256dh: string; auth: string }
  | null {
  const endpoint = String(body?.endpoint ?? "").trim();
  const p256dh = String(body?.keys?.p256dh ?? "").trim();
  const auth = String(body?.keys?.auth ?? "").trim();
  if (
    !endpoint ||
    endpoint.length > ENDPOINT_LIMIT ||
    !/^https:\/\/./.test(endpoint) ||
    !p256dh ||
    p256dh.length > KEY_LIMIT ||
    !auth ||
    auth.length > KEY_LIMIT
  ) {
    return null;
  }
  return { endpoint, p256dh, auth };
}

async function readBody(request: Request): Promise<SubscribeBody | null> {
  try {
    return (await request.json()) as SubscribeBody;
  } catch {
    return null;
  }
}

export async function POST(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  if (!pushConfigured()) {
    return NextResponse.json({ error: "push_not_configured" }, { status: 503 });
  }
  const parsed = parseBody((await readBody(request)) ?? {});
  if (!parsed) {
    return NextResponse.json({ error: "invalid_subscription" }, { status: 400 });
  }
  try {
    await addSubscription({
      endpoint: parsed.endpoint,
      keys: { p256dh: parsed.p256dh, auth: parsed.auth },
      user_agent: request.headers.get("user-agent"),
      created_at: new Date().toISOString(),
    });
  } catch (err) {
    return NextResponse.json(
      { error: "push_store_error", detail: err instanceof Error ? err.message : "" },
      { status: 502 },
    );
  }
  return NextResponse.json({ status: "subscribed" });
}

export async function DELETE(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal && !verifyAppPushSecret(request)) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const body = (await readBody(request)) ?? {};
  const endpoint = String(body?.endpoint ?? "").trim();
  if (!endpoint || endpoint.length > ENDPOINT_LIMIT) {
    return NextResponse.json({ error: "invalid_subscription" }, { status: 400 });
  }
  try {
    const removed = await removeSubscription(endpoint);
    return NextResponse.json({ status: removed ? "removed" : "not_found" });
  } catch (err) {
    return NextResponse.json(
      { error: "push_store_error", detail: err instanceof Error ? err.message : "" },
      { status: 502 },
    );
  }
}
