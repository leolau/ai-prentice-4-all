/**
 * PATCH /api/projects/:slug/outputs/:outputId — edit an output; flipping
 * `required` is structural and lead-only upstream.
 * DELETE — remove it; the last required output is protected (§6.1).
 */
import { NextResponse } from "next/server";

import { readBody, withPrincipal } from "../../../hermes-bridge";

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ slug: string; outputId: string }> },
): Promise<NextResponse> {
  const { slug, outputId } = await params;
  const body = await readBody(req);
  if (Object.keys(body).length === 0) {
    return NextResponse.json(
      { error: "invalid_request", detail: "Nothing to patch." },
      { status: 400 },
    );
  }
  return withPrincipal((client) =>
    client.updateProjectOutput(slug, outputId, body),
  );
}

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ slug: string; outputId: string }> },
): Promise<NextResponse> {
  const { slug, outputId } = await params;
  return withPrincipal((client) => client.deleteProjectOutput(slug, outputId));
}
