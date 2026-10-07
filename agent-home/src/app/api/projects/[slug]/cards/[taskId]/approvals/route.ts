/**
 * POST /api/projects/:slug/cards/:taskId/approvals — answer an approval the
 * card's worker asked for: `{ key, decision: "approve" | "deny" }`.
 */
import { NextResponse } from "next/server";

import { readBody, withPrincipal } from "../../../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string; taskId: string }> },
): Promise<NextResponse> {
  const { slug, taskId } = await params;
  const body = await readBody(req);
  const key = typeof body.key === "string" ? body.key.trim() : "";
  const decision = body.decision;
  if (!key || (decision !== "approve" && decision !== "deny")) {
    return NextResponse.json(
      { detail: "key and a decision of 'approve' or 'deny' are required" },
      { status: 422 },
    );
  }
  return withPrincipal((client) =>
    client.decideProjectCardApproval(slug, taskId, key, decision),
  );
}
