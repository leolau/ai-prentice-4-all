/**
 * GET /api/projects/:slug — the whole record in one read (design §12).
 * PATCH /api/projects/:slug — record fields, lead/admin (§11).
 */
import { NextResponse } from "next/server";

import { readBody, withPrincipal } from "../hermes-bridge";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  return withPrincipal((client) => client.project(slug));
}

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  if (Object.keys(body).length === 0) {
    return NextResponse.json(
      { error: "invalid_request", detail: "Nothing to patch." },
      { status: 400 },
    );
  }
  return withPrincipal((client) => client.updateProject(slug, body));
}
