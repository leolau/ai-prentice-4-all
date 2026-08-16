/**
 * PATCH /api/projects/:slug/autonomy — its own route so the audit line and
 * the permission check are unmistakable (§12). Lead/admin only upstream.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../hermes-bridge";

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const autonomy = String(body.autonomy ?? "").trim();
  if (!autonomy) return invalidRequest("An autonomy level is required.");
  return withPrincipal((client) => client.setProjectAutonomy(slug, autonomy));
}
