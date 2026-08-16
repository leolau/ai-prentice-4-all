/**
 * POST /api/projects/:slug/runs/:runNo/retro — write or edit the retrospective.
 * (`score_self` lands with step 9b; `score_user` is human-only there.)
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
  const retro = String(body.retro ?? "").trim();
  if (!retro) return invalidRequest("A retro needs some text.");
  return withPrincipal((client) =>
    client.writeProjectRetro(slug, runNoInt, retro),
  );
}
