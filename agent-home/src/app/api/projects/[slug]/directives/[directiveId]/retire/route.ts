/**
 * POST /api/projects/:slug/directives/:directiveId/retire — retire, never
 * delete (§5.2): the historical record survives.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../../hermes-bridge";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ slug: string; directiveId: string }> },
): Promise<NextResponse> {
  const { slug, directiveId } = await params;
  return withPrincipal((client) =>
    client.retireProjectDirective(slug, directiveId),
  );
}
