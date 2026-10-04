/**
 * POST /api/projects/:slug/cards/:taskId/unblock — blocked → ready; an
 * optional `reason` lands on the card's thread first.
 */
import { NextResponse } from "next/server";

import { readBody, withPrincipal } from "../../../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string; taskId: string }> },
): Promise<NextResponse> {
  const { slug, taskId } = await params;
  const body = await readBody(req);
  const reason = typeof body.reason === "string" ? body.reason.trim() : "";
  return withPrincipal((client) =>
    client.unblockProjectCard(slug, taskId, reason || undefined),
  );
}
