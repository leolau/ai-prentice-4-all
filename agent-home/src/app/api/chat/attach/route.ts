/**
 * POST /api/chat/attach — re-attach to an in-flight turn. Proxies the Python
 * `POST /api/sessions/{id}/chat/stream/attach` SSE verbatim: the upstream
 * replays the turn's buffered events, then tails it live, so a reloaded page
 * resumes the stream instead of waiting for the transcript to catch up.
 */
import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { profileFromBody } from "@/lib/chat/profile";

interface AttachBody {
  sessionId?: unknown;
  runId?: unknown;
  profile?: unknown;
}

export async function POST(request: Request): Promise<Response> {
  const principal = await getPrincipal();
  if (!principal) {
    return new Response(
      JSON.stringify({ error: "unauthenticated", detail: "Sign in to continue." }),
      { status: 401, headers: { "content-type": "application/json" } },
    );
  }
  let body: AttachBody;
  try {
    body = (await request.json()) as AttachBody;
  } catch {
    return new Response(
      JSON.stringify({ error: "invalid_json", detail: "Malformed request body." }),
      { status: 400, headers: { "content-type": "application/json" } },
    );
  }
  const sessionId = typeof body.sessionId === "string" ? body.sessionId : "";
  const runId = typeof body.runId === "string" ? body.runId : "";
  if (!sessionId || !runId) {
    return new Response(
      JSON.stringify({ error: "missing_ids", detail: "sessionId and runId are required." }),
      { status: 400, headers: { "content-type": "application/json" } },
    );
  }
  try {
    const client = await apiClientForRequest({ profile: profileFromBody(body) });
    const upstream = await client.openAttachStream(sessionId, runId);
    return new Response(upstream.body, {
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache, no-transform",
        "x-accel-buffering": "no",
        "x-hermes-session-id": sessionId,
      },
    });
  } catch (err) {
    if (err instanceof HermesApiError) {
      return new Response(
        JSON.stringify({ error: "api_error", detail: err.message }),
        { status: err.status, headers: { "content-type": "application/json" } },
      );
    }
    return new Response(
      JSON.stringify({ error: "api_unreachable", detail: "The AI layer could not be reached." }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }
}
