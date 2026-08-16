/**
 * GET /api/projects/:slug/directives — standing instructions + feedback (§5).
 * POST — add guidance; applies from the next run, never mid-conversation (§5.1).
 * The active set is capped upstream — adding past the cap is a 409 *retire one first*.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../hermes-bridge";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const includeRetired =
    new URL(req.url).searchParams.get("include_retired") === "true";
  return withPrincipal((client) => client.projectDirectives(slug, { includeRetired }));
}

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const text = String(body.body ?? "").trim();
  if (!text) return invalidRequest("Guidance needs some text.");
  return withPrincipal((client) =>
    client.addProjectDirective(slug, {
      kind: body.kind !== undefined ? String(body.kind) : undefined,
      body: text,
      scope: body.scope !== undefined ? String(body.scope) : undefined,
      target_ref: body.target_ref !== undefined ? String(body.target_ref) : undefined,
      rating: body.rating !== undefined ? String(body.rating) : undefined,
    }),
  );
}
