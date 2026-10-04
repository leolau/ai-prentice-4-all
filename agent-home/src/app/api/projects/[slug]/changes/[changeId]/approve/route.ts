/**
 * POST /api/projects/:slug/changes/:changeId/approve `{rev, start?, supersedes?}`
 * — activate the drafted plan and start the next run (`start: false` saves
 * without running). 409 while a run is open.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string; changeId: string }> },
): Promise<NextResponse> {
  const { slug, changeId } = await params;
  const body = await readBody(req);
  const rev = Number(body.rev);
  if (!Number.isInteger(rev) || rev < 1) return invalidRequest("Pick a plan revision to approve.");
  const supersedes = Array.isArray(body.supersedes) ? body.supersedes.map(String) : undefined;
  const key = req.headers.get("idempotency-key") ?? undefined;
  return withPrincipal((client) =>
    client.approveProjectChange(
      slug,
      changeId,
      {
        rev,
        ...(body.start === false ? { start: false } : {}),
        ...(supersedes ? { supersedes } : {}),
      },
      key,
    ),
  );
}
