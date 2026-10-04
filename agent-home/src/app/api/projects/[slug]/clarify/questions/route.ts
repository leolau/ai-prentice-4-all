/**
 * POST /api/projects/:slug/clarify/questions — ask the agent for its next
 * round of questions, optionally `{focus}`. Returns at once; the job state
 * rides on the answer. The `Idempotency-Key` is forwarded.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../../hermes-bridge";

export const FOCUS_MAX = 500;

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const focus = String(body.focus ?? "").trim();
  if (focus.length > FOCUS_MAX) {
    return invalidRequest(`Keep the focus under ${FOCUS_MAX} characters.`);
  }
  const key = req.headers.get("idempotency-key") ?? undefined;
  return withPrincipal((client) =>
    client.askClarifyQuestions(slug, focus ? { focus } : {}, key),
  );
}
