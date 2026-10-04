/**
 * POST /api/projects/:slug/clarify/confirm — `{understanding?, draft_plan?}`:
 * confirm the agent's understanding of goal and scope (optionally edited)
 * and, with `draft_plan`, start the plan draft from it.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../../hermes-bridge";

export const UNDERSTANDING_MAX = 4000;

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const understanding =
    typeof body.understanding === "string" ? body.understanding.trim() : "";
  if (understanding.length > UNDERSTANDING_MAX) {
    return invalidRequest(`Keep the summary under ${UNDERSTANDING_MAX} characters.`);
  }
  const payload: { understanding?: string; draft_plan?: boolean } = {};
  if (understanding) payload.understanding = understanding;
  if (body.draft_plan === true) payload.draft_plan = true;
  const key = req.headers.get("idempotency-key") ?? undefined;
  return withPrincipal((client) => client.confirmClarify(slug, payload, key));
}
