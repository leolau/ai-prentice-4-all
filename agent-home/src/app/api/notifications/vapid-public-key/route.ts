/**
 * GET /api/notifications/vapid-public-key — the applicationServerKey the
 * browser needs for `pushManager.subscribe`. Generates the keypair on first
 * use (kept in the push store); the private half never leaves the server.
 */
import { NextResponse } from "next/server";

import { getPrincipal } from "@/lib/auth/principal";
import { ensureVapid, pushConfigured } from "@/lib/push/store";

export async function GET(): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  if (!pushConfigured()) {
    return NextResponse.json(
      { error: "push_not_configured" },
      { status: 503 },
    );
  }
  try {
    const doc = await ensureVapid();
    return NextResponse.json({ publicKey: doc.public_key });
  } catch (err) {
    return NextResponse.json(
      { error: "push_store_error", detail: err instanceof Error ? err.message : "" },
      { status: 502 },
    );
  }
}
