/**
 * POST /api/projects/:slug/cards/approve — Approve all N: `{task_ids}` made
 * ready in one request; the answer lists each card's outcome.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const raw = Array.isArray(body.task_ids) ? body.task_ids : [];
  const taskIds = [...new Set(raw.map((id) => String(id ?? "").trim()).filter(Boolean))];
  if (taskIds.length === 0) return invalidRequest("Pick at least one card to approve.");
  return withPrincipal((client) => client.approveProjectCards(slug, taskIds));
}
