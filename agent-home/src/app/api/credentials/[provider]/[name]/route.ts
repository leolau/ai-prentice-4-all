/**
 * Per-entry routes for Settings → Connected accounts: detail, toggles
 * (services / visibility), and disconnect. Writes act as the bridged
 * principal; the Python store enforces ownership.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

type RouteParams = { params: Promise<{ provider: string; name: string }> };

async function fail(err: unknown): Promise<NextResponse> {
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

export async function GET(
  _req: Request,
  { params }: RouteParams,
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { provider, name } = await params;
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.credential(provider, name));
  } catch (err) {
    return fail(err);
  }
}

export async function PATCH(
  req: Request,
  { params }: RouteParams,
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { provider, name } = await params;
  const body = (await req.json().catch(() => ({}))) as {
    services?: string[];
    visibility?: string;
  };
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(
      await client.patchCredential(provider, name, {
        services: body.services,
        visibility: body.visibility,
      }),
    );
  } catch (err) {
    return fail(err);
  }
}

export async function DELETE(
  _req: Request,
  { params }: RouteParams,
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { provider, name } = await params;
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.deleteCredential(provider, name));
  } catch (err) {
    return fail(err);
  }
}
