/**
 * GET /api/projects/:slug/runs/:runNo/stream — the run row pushed live,
 * proxied verbatim from the Python `GET /{slug}/runs/{n}/stream` SSE
 * stream (§12 push edition). Same payload `GET /runs/:runNo` returns; the
 * point of this route is that the browser doesn't have to ask for it again
 * on a timer to find out it changed.
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
  { params }: { params: Promise<{ slug: string; runNo: string }> },
): Promise<Response> {
  const principal = await getPrincipal();
  if (!principal) {
    return jsonError("unauthenticated", "Sign in to continue.", 401);
  }
  const { slug, runNo } = await params;
  const runNoInt = Number(runNo);
  if (!Number.isInteger(runNoInt)) {
    return jsonError("invalid_request", "Run number must be an integer.", 400);
  }
  try {
    const client = await apiClientForRequest();
    const upstream = await client.openRunStream(slug, runNoInt);
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
