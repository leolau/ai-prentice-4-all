/**
 * POST /api/credentials/google/start — begin a Google OAuth connect.
 *
 * Body: { name?: email hint, services: ["email"|"calendar"|"workspace", …] }.
 * Returns the consent URL; the Python side holds the PKCE pending state.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

export async function POST(req: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const body = (await req.json().catch(() => ({}))) as {
    name?: string;
    services?: string[];
  };
  const services = Array.isArray(body.services) ? body.services : [];
  if (services.length === 0) {
    return NextResponse.json(
      { error: "invalid_request", detail: "Pick at least one service." },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(
      await client.googleStart({ name: body.name, services }),
    );
  } catch (err) {
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
}
