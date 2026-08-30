/**
 * GET /api/notifications/config — the service-to-service seam the Python
 * app-channel sender pulls before fanning out a Web Push: the VAPID private
 * key plus every enrolled subscription. Guarded by the shared app-push
 * secret (loopback callers still need it — no principal cookies here).
 */
import { NextResponse } from "next/server";

import { verifyAppPushSecret } from "@/lib/push/secret";
import { ensureVapid, listSubscriptions, pushConfigured } from "@/lib/push/store";

export async function GET(request: Request): Promise<NextResponse> {
  if (!verifyAppPushSecret(request)) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  if (!pushConfigured()) {
    return NextResponse.json({ error: "push_not_configured" }, { status: 404 });
  }
  try {
    // ensureVapid heals the pre-DER-fix PEM documents; the keypair is
    // created/regenerated here rather than failing the sender.
    const vapid = await ensureVapid();
    const subscriptions = await listSubscriptions();
    return NextResponse.json({
      vapid_private_key: vapid.private_key,
      subscriptions: subscriptions.map((s) => ({
        endpoint: s.endpoint,
        keys: s.keys,
      })),
    });
  } catch (err) {
    return NextResponse.json(
      { error: "push_store_error", detail: err instanceof Error ? err.message : "" },
      { status: 502 },
    );
  }
}
