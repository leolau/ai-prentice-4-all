/**
 * POST /api/projects/:slug/summarise — the rolling "where this stands"
 * (design §2.2, §17 step 11). Body: `{ summary }`. Writes `summary` and
 * stamps `summary_at`; overwritten each time, never appended.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const summary = String(body.summary ?? "").trim();
  if (!summary) return invalidRequest("Summary needs some text.");
  return withPrincipal((client) => client.summariseProject(slug, summary));
}
