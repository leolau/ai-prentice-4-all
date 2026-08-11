/**
 * GET /api/goals/entity — the entity goal, as the settings page shows it.
 * PATCH /api/goals/entity — the owner editing it.
 *
 * Forwards to the Python `/api/registry/goals/entity` under the bridged C1
 * principal. Authorisation is *not* re-implemented here: the Python layer
 * refuses a non-owner write, so a caller who skips this BFF gains nothing.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

function failure(err: unknown): NextResponse {
  if (err instanceof HermesApiError) {
    return NextResponse.json(
      { error: "api_error", detail: err.message },
      { status: err.status },
    );
  }
  return NextResponse.json(
    { error: "api_unreachable", detail: "The AI layer could not be reached." },
    { status: 502 },
  );
}

export async function GET(): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.entityGoal());
  } catch (err) {
    return failure(err);
  }
}

export async function PATCH(req: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const body = (await req.json().catch(() => ({}))) as {
    title?: string;
    description?: string;
  };
  const title = (body.title ?? "").trim();
  if (!title && body.description === undefined) {
    return NextResponse.json(
      { error: "invalid", detail: "nothing to update" },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(
      await client.updateEntityGoal({
        title: title || undefined,
        description: body.description,
      }),
    );
  } catch (err) {
    return failure(err);
  }
}
