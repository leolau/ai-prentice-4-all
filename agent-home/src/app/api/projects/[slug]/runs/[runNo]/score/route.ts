/**
 * POST /api/projects/:slug/runs/:runNo/score — score a run 1–5 (§8.1).
 * Human-only upstream: the BFF forwards the user's verified session, so a
 * logged-in tap goes through and a session-less caller is refused there.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string; runNo: string }> },
): Promise<NextResponse> {
  const { slug, runNo } = await params;
  const runNoInt = Number(runNo);
  if (!Number.isInteger(runNoInt)) {
    return invalidRequest("Run number must be an integer.");
  }
  const body = await readBody(req);
  const score = body.score;
  if (
    typeof score !== "number" ||
    !Number.isInteger(score) ||
    score < 1 ||
    score > 5
  ) {
    return invalidRequest("A score is a number from 1 to 5.");
  }
  const note = String(body.note ?? "").trim() || undefined;
  return withPrincipal((client) =>
    client.scoreProjectRun(slug, runNoInt, { score, note }),
  );
}
