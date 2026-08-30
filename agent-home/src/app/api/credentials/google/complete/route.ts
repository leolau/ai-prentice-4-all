/**
 * POST /api/credentials/google/complete — finish a Google OAuth connect.
 *
 * Body: { code_or_url } — the bare authorization code or the full
 * localhost redirect URL pasted from the browser. The response echoes the
 * granted account + scopes so a wrong-account consent is visible before the
 * entry is relied upon.
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
    code_or_url?: string;
  };
  const codeOrUrl = (body.code_or_url ?? "").trim();
  if (!codeOrUrl) {
    return NextResponse.json(
      { error: "invalid_request", detail: "Paste the code or redirect URL." },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.googleComplete({ code_or_url: codeOrUrl }));
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
