/**
 * POST /api/projects/:slug/cards/:taskId/stop — halt the card without a
 * re-run: any live worker is terminated and the card parks in blocked.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../../hermes-bridge";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ slug: string; taskId: string }> },
): Promise<NextResponse> {
  const { slug, taskId } = await params;
  return withPrincipal((client) => client.stopProjectCard(slug, taskId));
}
