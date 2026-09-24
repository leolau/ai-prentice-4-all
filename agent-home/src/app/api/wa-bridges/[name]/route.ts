/**
 * /api/wa-bridges/[name] — per-bridge actions. POST {action} where action is
 * "restart" | "stop" | "rebind" (wipe session, restart, fresh QR).
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

const ACTIONS = new Set(["restart", "stop", "rebind"]);

export async function POST(
  req: Request,
  { params }: { params: Promise<{ name: string }> },
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { name } = await params;
  let body: { action?: string };
  try {
    body = (await req.json()) as { action?: string };
  } catch {
    return NextResponse.json({ error: "bad_json" }, { status: 400 });
  }
  const action = body.action ?? "";
  if (!ACTIONS.has(action)) {
    return NextResponse.json({ error: "bad_action" }, { status: 400 });
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(
      await client.waBridgeAction(name, action as "restart" | "stop" | "rebind"),
    );
  } catch (err) {
    if (err instanceof HermesApiError) {
      return NextResponse.json(
        { error: "api_error", detail: err.message },
        { status: err.status },
      );
    }
    return NextResponse.json(
      { error: "api_unreachable", detail: "The AI layer could not be reached." },
      { status: 502 },
    );
  }
}
