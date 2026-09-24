/**
 * GET /api/projects/:slug/events/stream — the project's event cursor
 * pushed live, proxied verbatim from the Python
 * `GET /{slug}/events/stream` SSE stream (§12 push edition). Replaces the
 * project page's poll of `GET /events` on a timer: the same
 * `latest_event_id`, sent again only when it moves. Never ends on its
 * own — the caller tears the connection down on unmount.
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
  { params }: { params: Promise<{ slug: string }> },
): Promise<Response> {
  const principal = await getPrincipal();
  if (!principal) {
    return jsonError("unauthenticated", "Sign in to continue.", 401);
  }
  const { slug } = await params;
  try {
    const client = await apiClientForRequest();
    const upstream = await client.openProjectEventsStream(slug);
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
