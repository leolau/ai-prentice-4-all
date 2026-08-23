/**
 * POST /api/app-mcp/ticket — mint a short-lived ticket for the app-mcp
 * WebSocket.
 *
 * The browser bridge connects to the standalone app-mcp service, which sits
 * OUTSIDE the cookie domain. Instead of sharing the session cookie, the BFF
 * (which owns authentication) signs a 60-second ticket
 * `<user_id>.<expiry_ms>.<hmac>` with a secret the app-mcp service also
 * knows. One ticket, one connection attempt; a leaked ticket is useless
 * after the window and grants exactly what the session already grants.
 *
 * 503 when the shared secret isn't configured — the bridge treats that as
 * "service absent" and retries quietly, so an unconfigured box degrades
 * gracefully.
 */
import { createHmac } from "crypto";

import { getPrincipal } from "@/lib/auth/principal";
import { appMcpSecret } from "@/lib/env";

export const TICKET_TTL_MS = 60_000;

export function signTicket(payload: string, secret: string): string {
  return createHmac("sha256", secret).update(payload).digest("base64url");
}

function json(body: Record<string, unknown>, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export async function POST(): Promise<Response> {
  const principal = await getPrincipal();
  if (!principal) {
    return json({ error: "unauthenticated", detail: "Sign in to continue." }, 401);
  }
  const secret = appMcpSecret();
  if (!secret) {
    return json(
      { error: "app_mcp_not_configured", detail: "app-mcp is not configured on this box." },
      503,
    );
  }
  const expires = Date.now() + TICKET_TTL_MS;
  const payload = `${principal.user_id}.${expires}`;
  const ticket = `${payload}.${signTicket(payload, secret)}`;
  return json({ ticket, wsPath: "/app-mcp/ws", expires }, 200);
}
