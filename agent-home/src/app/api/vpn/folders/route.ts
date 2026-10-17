/**
 * GET/POST /api/vpn/folders — BFF for Files → Bridge → Network folders.
 *
 * Forwards to the Python `/api/vpn/folders` under the bridged principal;
 * Hermes scopes the list to the caller's own `smb-folder` entries, checks the
 * chosen VPN is theirs to use, and never returns the share password.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { parseNetworkFolderBody } from "@/lib/folder-bridge/networkFolders";

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

export async function GET(): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.networkFolders());
  } catch (err) {
    return fail(err);
  }
}

export async function POST(req: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const parsed = parseNetworkFolderBody(await req.json().catch(() => null));
  if (parsed.error !== null) {
    return NextResponse.json({ error: "bad_request", detail: parsed.error }, { status: 400 });
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.createNetworkFolder(parsed.input), {
      status: 201,
    });
  } catch (err) {
    return fail(err);
  }
}
