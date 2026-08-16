/**
 * GET /api/projects/:slug/runs — the record (§6): every run with duration,
 * outcome, deliveries and scores.
 * POST — start a run now (`trigger='manual'`); `playbook_rev` repeats an old
 * method — "do exactly what worked last time" (§7.2).
 */
import { NextResponse } from "next/server";

import { readBody, withPrincipal } from "../../hermes-bridge";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  return withPrincipal((client) => client.projectRuns(slug));
}

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const playbookRev = body.playbook_rev;
  return withPrincipal((client) =>
    client.startProjectRun(slug, {
      playbook_rev:
        playbookRev !== undefined && playbookRev !== null
          ? Number(playbookRev)
          : undefined,
    }),
  );
}
