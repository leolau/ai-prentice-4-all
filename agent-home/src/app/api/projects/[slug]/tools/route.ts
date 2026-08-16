/**
 * PATCH /api/projects/:slug/tools — set toolsets/skills; the response is the
 * resolved intersection with the host profile (§4.1), so the UI shows what
 * would *actually* run, including what the profile refused.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../hermes-bridge";

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const { toolsets, skills } = body as {
    toolsets?: string[];
    skills?: string[];
  };
  if (!Array.isArray(toolsets) && !Array.isArray(skills)) {
    return invalidRequest("Provide toolsets and/or skills.");
  }
  return withPrincipal((client) =>
    client.setProjectTools(slug, { toolsets, skills }),
  );
}
