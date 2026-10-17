/**
 * PUT/DELETE /api/vpn/folders/{name} — edit (incl. rename via body `name`;
 * a blank share password keeps the stored one) or remove (Hermes unmounts
 * first) one of the caller's network folders.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { parseNetworkFolderBody } from "@/lib/folder-bridge/networkFolders";

type RouteParams = { params: Promise<{ name: string }> };

function fail(err: unknown): NextResponse {
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

export async function PUT(req: Request, { params }: RouteParams): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { name } = await params;
  const parsed = parseNetworkFolderBody(await req.json().catch(() => null));
  if (parsed.error !== null) {
    return NextResponse.json({ error: "bad_request", detail: parsed.error }, { status: 400 });
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.updateNetworkFolder(name, parsed.input));
  } catch (err) {
    return fail(err);
  }
}

export async function DELETE(_req: Request, { params }: RouteParams): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { name } = await params;
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.deleteNetworkFolder(name));
  } catch (err) {
    return fail(err);
  }
}
