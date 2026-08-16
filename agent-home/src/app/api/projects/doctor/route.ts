/**
 * GET /api/projects/doctor — diagnosable breaks across readable projects
 * (§15 failure mode 1): a broken schedule is invisible otherwise — it simply
 * never runs. `?slug=` narrows to one project.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../hermes-bridge";

export async function GET(req: Request): Promise<NextResponse> {
  const slug = new URL(req.url).searchParams.get("slug") ?? undefined;
  return withPrincipal((client) => client.projectsDoctor(slug));
}
