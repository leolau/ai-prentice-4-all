/**
 * GET /api/projects/:slug/board/context — which run created each card and
 * whether the open run is stalled (the board's truthful empty states).
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../hermes-bridge";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  return withPrincipal((client) => client.projectBoardContext(slug));
}
