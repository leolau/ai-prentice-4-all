/** POST /api/projects/:slug/changes/:changeId/draft — draft the revised plan again. */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../../hermes-bridge";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ slug: string; changeId: string }> },
): Promise<NextResponse> {
  const { slug, changeId } = await params;
  return withPrincipal((client) => client.redraftProjectChange(slug, changeId));
}
