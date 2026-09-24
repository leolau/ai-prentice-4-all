/**
 * /api/wa-bridges/[name]/qr — the pending pairing QR as an SVG. The raw
 * payload is authentication material, so it stays server-side: the BFF
 * fetches it from the Python API and renders it to an image here.
 */
import { NextResponse } from "next/server";
import QRCode from "qrcode";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ name: string }> },
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { name } = await params;
  try {
    const client = await apiClientForRequest();
    const qr = await client.waBridgeQr(name);
    const svg = await QRCode.toString(qr.payload, {
      type: "svg",
      margin: 1,
      width: 256,
    });
    return NextResponse.json({
      svg,
      age_seconds: qr.age_seconds,
      stale: qr.stale,
    });
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
