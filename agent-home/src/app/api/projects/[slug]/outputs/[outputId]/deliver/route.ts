/**
 * POST /api/projects/:slug/outputs/:outputId/deliver — record a delivery
 * (a run, a card, or a hand-attached artefact). Marks the output delivered.
 */
import { NextResponse } from "next/server";

import { readBody, withPrincipal } from "../../../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string; outputId: string }> },
): Promise<NextResponse> {
  const { slug, outputId } = await params;
  const body = await readBody(req);
  const str = (key: string) =>
    body[key] !== undefined ? String(body[key]) : undefined;
  return withPrincipal((client) =>
    client.deliverProjectOutput(slug, outputId, {
      run_id: str("run_id"),
      task_id: str("task_id"),
      link_kind: str("link_kind"),
      link_ref: str("link_ref"),
      profile: str("profile"),
      label: str("label"),
      note: str("note"),
    }),
  );
}
