/**
 * GET /api/projects/:slug/board — the project's cards, column-grouped through
 * the one shared rollup helper (design §12), filtered by the caller's
 * principal so another user's `private:` card stays invisible.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../hermes-bridge";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  return withPrincipal((client) => client.projectBoard(slug));
}
