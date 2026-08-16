/**
 * POST /api/projects/:slug/links — attach a pointer (§11 rule 5): a file,
 * arrival, to-do, goal, memory document, conversation, sample, reference, or
 * plain URL. A link is a pointer, never a copy — the authority stays in the
 * owning profile.
 * DELETE — detach a pointer.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const kind = String(body.kind ?? "").trim();
  const ref = String(body.ref ?? "").trim();
  if (!kind || !ref) return invalidRequest("A link needs a kind and a ref.");
  return withPrincipal((client) =>
    client.linkToProject(slug, {
      kind,
      ref,
      profile: body.profile !== undefined ? String(body.profile) : undefined,
      label: body.label !== undefined ? String(body.label) : undefined,
    }),
  );
}

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const kind = String(body.kind ?? "").trim();
  const ref = String(body.ref ?? "").trim();
  if (!kind || !ref) return invalidRequest("A link needs a kind and a ref.");
  return withPrincipal((client) =>
    client.unlinkFromProject(slug, {
      kind,
      ref,
      profile: body.profile !== undefined ? String(body.profile) : undefined,
    }),
  );
}
