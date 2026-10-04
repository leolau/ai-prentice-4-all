/**
 * GET /api/projects/:slug/cards/:taskId/activity — a board-dispatched card's
 * live reasoning and tool activity, proxied verbatim from the Python
 * `GET /{slug}/cards/{id}/activity` SSE stream (the worker's own process
 * publishes it). `?after=` resumes from a sequence number. Only reasoning
 * text and a tool's id and name cross this boundary.
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
  request: Request,
  { params }: { params: Promise<{ slug: string; taskId: string }> },
): Promise<Response> {
  const principal = await getPrincipal();
  if (!principal) {
    return jsonError("unauthenticated", "Sign in to continue.", 401);
  }
  const { slug, taskId } = await params;
  const rawAfter = new URL(request.url).searchParams.get("after");
  const after = Number(rawAfter ?? 0);
  if (!Number.isInteger(after) || after < 0) {
    return jsonError("invalid_request", "after must be a whole number.", 400);
  }
  try {
    const client = await apiClientForRequest();
    const upstream = await client.openCardActivityStream(slug, taskId, after);
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
