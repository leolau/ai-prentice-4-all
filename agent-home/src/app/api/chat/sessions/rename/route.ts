/**
 * PATCH /api/chat/sessions/rename — BFF rename a conversation (FG-20 Wave C1).
 * Forwards `{ sessionId, title }` to the Python API `PATCH /api/sessions/{id}`
 * under the bridged C1 principal. An empty title clears the name; a duplicate
 * title is rejected upstream with 400.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

export async function PATCH(req: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  let body: { sessionId?: unknown; title?: unknown };
  try {
    body = (await req.json()) as { sessionId?: unknown; title?: unknown };
  } catch {
    return NextResponse.json({ error: "invalid_body" }, { status: 400 });
  }
  const sessionId = typeof body.sessionId === "string" ? body.sessionId : "";
  const title = typeof body.title === "string" ? body.title : "";
  if (!sessionId) {
    return NextResponse.json({ error: "missing_session" }, { status: 400 });
  }
  try {
    const client = await apiClientForRequest();
    const data = await client.renameSession(sessionId, title);
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
