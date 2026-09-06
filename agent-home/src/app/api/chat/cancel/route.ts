/**
 * POST /api/chat/cancel — stop an in-flight chat turn. Body: `{ sessionId,
 * runId, profile? }`. Forwards to the Python
 * `POST /api/sessions/{id}/chat/stream/cancel`, which interrupts the agent
 * turn. Closing the SSE connection alone does not stop a turn (that is what
 * lets it survive a closed tab), so the Stop button needs this explicit verb.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { profileFromBody } from "@/lib/chat/profile";

interface CancelBody {
  sessionId?: unknown;
  runId?: unknown;
  profile?: unknown;
}

export async function POST(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json(
      { error: "unauthenticated", detail: "Sign in to continue." },
      { status: 401 },
    );
  }
  let body: CancelBody;
  try {
    body = (await request.json()) as CancelBody;
  } catch {
    return NextResponse.json(
      { error: "invalid_json", detail: "Malformed request body." },
      { status: 400 },
    );
  }
  const sessionId = typeof body.sessionId === "string" ? body.sessionId : "";
  const runId = typeof body.runId === "string" ? body.runId : "";
  if (!sessionId || !runId) {
    return NextResponse.json(
      { error: "missing_ids", detail: "sessionId and runId are required." },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest({ profile: profileFromBody(body) });
    const resp = await client.cancelChatRun(sessionId, runId);
    return NextResponse.json(resp);
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
