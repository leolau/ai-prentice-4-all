/**
 * POST /api/projects/:slug/outputs — add a deliverable [3].
 *
 * The output list itself rides with the detail read (`GET /api/projects/:slug`)
 * — there is no standalone GET upstream. Delete refuses on the last required
 * output (§6.1).
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
  if (!title) return invalidRequest("An output needs a title.");
  return withPrincipal((client) =>
    client.addProjectOutput(slug, {
      title,
      spec: body.spec !== undefined ? String(body.spec) : undefined,
      kind: body.kind !== undefined ? String(body.kind) : undefined,
      required: body.required !== undefined ? Boolean(body.required) : undefined,
      recurring: body.recurring !== undefined ? Boolean(body.recurring) : undefined,
    }),
  );
}
