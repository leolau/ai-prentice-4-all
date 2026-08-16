/**
 * POST /api/projects/:slug/cards — create a card carrying the project id.
 * Cards made through the Projects surface land in `triage`: a project asking
 * for work is not the same as a human approving it (§10). The `from_todo`
 * seam lands in step 8b.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const title = String(body.title ?? "").trim();
  if (!title) return invalidRequest("A card needs a title.");
  return withPrincipal((client) =>
    client.createProjectCard(slug, {
      title,
      body: body.body !== undefined ? String(body.body) : undefined,
      assignee: body.assignee !== undefined ? String(body.assignee) : undefined,
      from_todo: body.from_todo !== undefined ? String(body.from_todo) : undefined,
    }),
  );
}
