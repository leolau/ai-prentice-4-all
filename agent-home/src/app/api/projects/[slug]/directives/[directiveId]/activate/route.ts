/**
 * POST /api/projects/:slug/directives/:directiveId/activate — a member
 * activates a directive a run proposed in its retro (§8.2). Applies from
 * the next run; the active-set cap can refuse the crossing with a 409.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../../hermes-bridge";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ slug: string; directiveId: string }> },
): Promise<NextResponse> {
  const { slug, directiveId } = await params;
  return withPrincipal((client) =>
    client.activateProjectDirective(slug, directiveId),
  );
}
