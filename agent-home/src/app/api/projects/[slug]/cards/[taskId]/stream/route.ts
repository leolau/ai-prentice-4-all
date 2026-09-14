/**
 * GET /api/projects/:slug/cards/:taskId/stream — one card's row pushed
 * live while a worker is on it, proxied verbatim from the Python
 * `GET /{slug}/cards/{task_id}/stream` SSE stream (§12 push edition). Same
 * payload `GET /cards/:taskId` returns — including its heartbeat note and
 * comment thread — ending once the card leaves `running`.
 */
import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

function jsonError(error: string, detail: string, status: number): Response {
  return new Response(JSON.stringify({ error, detail }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ slug: string; taskId: string }> },
): Promise<Response> {
  const principal = await getPrincipal();
  if (!principal) {
    return jsonError("unauthenticated", "Sign in to continue.", 401);
  }
  const { slug, taskId } = await params;
  try {
    const client = await apiClientForRequest();
    const upstream = await client.openCardStream(slug, taskId);
    return new Response(upstream.body, {
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache, no-transform",
        "x-accel-buffering": "no",
      },
    });
  } catch (err) {
    if (err instanceof HermesApiError) {
      return jsonError("api_error", err.message, err.status);
    }
    return jsonError("api_unreachable", "The AI layer could not be reached.", 502);
  }
}
