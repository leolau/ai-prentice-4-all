/**
 * Shared plumbing for the `/api/projects` BFF mirror (design ed.3.2 §13).
 *
 * The todos/incomings routers inline their principal gate + error mapping per
 * route; the projects mirror has ~30 endpoints, so the identical boilerplate
 * lives here once. A route keeps its own validation — only auth and the
 * upstream error translation are shared.
 */
import { NextResponse } from "next/server";

import { HermesApiClient, HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

/**
 * Run a handler under the bridged principal: 401 without a session, upstream
 * `HermesApiError` statuses passed through, anything else a 502.
 */
export async function withPrincipal<T>(
  handler: (client: HermesApiClient) => Promise<T>,
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await handler(client));
  } catch (err) {
    if (err instanceof HermesApiError) {
      // Forward the upstream's own copy (F6): the design's refusal text —
      // *retire one first*, the budget refusal — sits in ``err.body.detail``;
      // ``err.message`` is the internal path + status and never reaches a
      // user. No detail upstream means generic copy, never the topology.
      const detail =
        err.body &&
        typeof (err.body as { detail?: unknown }).detail === "string"
          ? (err.body as { detail: string }).detail
          : "That didn't go through.";
      return NextResponse.json(
        { error: "api_error", detail },
        { status: err.status },
      );
    }
    return NextResponse.json(
      { error: "api_unreachable", detail: "The AI layer could not be reached." },
      { status: 502 },
    );
  }
}

/** The JSON body, or `{}` when the request carries none (or a broken one). */
export async function readBody(req: Request): Promise<Record<string, unknown>> {
  return (await req.json().catch(() => ({}))) as Record<string, unknown>;
}

/** A 400 for a missing mandatory body field, worded like the todos routes. */
export function invalidRequest(detail: string): NextResponse {
  return NextResponse.json(
    { error: "invalid_request", detail },
    { status: 400 },
  );
}
