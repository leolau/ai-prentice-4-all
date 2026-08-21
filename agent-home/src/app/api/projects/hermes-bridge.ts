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
      const rawDetail = err.body
        ? (err.body as { detail?: unknown }).detail
        : undefined;
      let detail = "That didn't go through.";
      let extra: Record<string, unknown> | null = null;
      if (typeof rawDetail === "string" && rawDetail) {
        detail = rawDetail;
      } else if (
        rawDetail &&
        typeof rawDetail === "object" &&
        !Array.isArray(rawDetail)
      ) {
        // A structured refusal — the create route's `{missing, message}`
        // (§13, U3). `detail` stays a string for every existing caller
        // while the object's fields ride alongside, so the create form can
        // map the 422 onto the field that is blank.
        if (typeof (rawDetail as { message?: unknown }).message === "string") {
          detail = (rawDetail as { message: string }).message;
        }
        const rest: Record<string, unknown> = {};
        for (const [key, value] of Object.entries(
          rawDetail as Record<string, unknown>,
        )) {
          if (key !== "detail" && key !== "message") rest[key] = value;
        }
        extra = rest;
      }
      return NextResponse.json(
        extra
          ? { error: "api_error", detail, ...extra }
          : { error: "api_error", detail },
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
