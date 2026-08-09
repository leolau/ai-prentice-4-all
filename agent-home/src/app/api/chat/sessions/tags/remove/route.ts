/**
 * POST /api/chat/sessions/tags/remove — BFF detach a tag from a session.
 * Body: `{ sessionId, tagId }`. Forwards to the Python API
 * `DELETE /api/sessions/{id}/tags/{tagId}` under the bridged C1 principal.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

export async function POST(req: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  let body: { sessionId?: unknown; tagId?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "invalid_body" }, { status: 400 });
  }
  const sessionId = typeof body.sessionId === "string" ? body.sessionId : "";
  const tagId = typeof body.tagId === "string" ? body.tagId : "";
  if (!sessionId || !tagId) {
    return NextResponse.json(
      { error: "missing_params", detail: "sessionId and tagId are required" },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest();
    const data = await client.removeSessionTag(sessionId, tagId);
    return NextResponse.json(data);
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
