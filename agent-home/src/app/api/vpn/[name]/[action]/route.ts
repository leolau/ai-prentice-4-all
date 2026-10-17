/**
 * POST /api/vpn/{name}/{connect|disconnect|test|trust} — tunnel actions for
 * Settings → In&Out → Private networks, run as the bridged principal (the
 * Python layer enforces who may use the VPN). A failed connection is a 200
 * with `status.state == "error"`; only bad input / unknown names are errors.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

type RouteParams = { params: Promise<{ name: string; action: string }> };

const SHA256_RE = /^[0-9a-f]{64}$/;

export async function POST(
  req: Request,
  { params }: RouteParams,
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { name, action } = await params;
  try {
    const client = await apiClientForRequest();
    if (action === "trust") {
      const body = (await req.json().catch(() => ({}))) as { sha256?: unknown };
      const sha256 =
        typeof body.sha256 === "string" ? body.sha256.toLowerCase() : "";
      if (!SHA256_RE.test(sha256)) {
        return NextResponse.json(
          { error: "bad_request", detail: "sha256 must be 64 hex characters" },
          { status: 400 },
        );
      }
      return NextResponse.json(await client.trustVpnCert(name, sha256));
    }
    if (action === "connect" || action === "disconnect" || action === "test") {
      return NextResponse.json(await client.vpnAction(name, action));
    }
    return NextResponse.json({ error: "not_found" }, { status: 404 });
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
