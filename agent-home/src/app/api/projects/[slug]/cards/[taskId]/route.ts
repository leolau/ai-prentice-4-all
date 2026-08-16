/**
 * GET /api/projects/:slug/cards/:taskId — one card, re-checked under the
 * caller's principal: a `private:` card owned by someone else is a 404
 * through the project surface too.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../hermes-bridge";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string; taskId: string }> },
): Promise<NextResponse> {
  const { slug, taskId } = await params;
  return withPrincipal((client) => client.projectCard(slug, taskId));
}
