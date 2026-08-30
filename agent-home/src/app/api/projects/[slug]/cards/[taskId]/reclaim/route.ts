/**
 * POST /api/projects/:slug/cards/:taskId/reclaim — stop a stuck worker and
 * re-queue the card; the dispatcher respawns on its next tick. Also the
 * retry path for blocked cards.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../../hermes-bridge";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ slug: string; taskId: string }> },
): Promise<NextResponse> {
  const { slug, taskId } = await params;
  return withPrincipal((client) => client.reclaimProjectCard(slug, taskId));
}
