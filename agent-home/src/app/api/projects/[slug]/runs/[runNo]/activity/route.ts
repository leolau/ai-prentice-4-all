/**
 * GET /api/projects/:slug/runs/:runNo/activity — the run's live reasoning and
 * tool activity, proxied verbatim from the Python
 * `GET /{slug}/runs/{n}/activity` SSE stream. `?after=` resumes from a
 * sequence number, so a reconnect replays what it missed instead of starting
 * blank. Only reasoning text and a tool's id and name cross this boundary;
 * tool arguments and results never leave the box.
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
  const rawAfter = new URL(request.url).searchParams.get("after");
  const after = Number(rawAfter ?? 0);
  if (!Number.isInteger(after) || after < 0) {
    return jsonError("invalid_request", "after must be a whole number.", 400);
  }
  try {
    const client = await apiClientForRequest();
    const upstream = await client.openRunActivityStream(slug, runNoInt, after);
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
