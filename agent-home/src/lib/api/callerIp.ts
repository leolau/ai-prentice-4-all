/**
 * The address a request to this BFF came from, for the unauthenticated
 * invitation endpoints.
 *
 * Next.js exposes no peer address, so this reads what Caddy set:
 * `X-Forwarded-For`'s **first** hop is the original client, and `X-Real-IP` is
 * the fallback. Empty when neither is present (a direct call in development),
 * which makes the upstream throttle fall back to the peer address rather than
 * trusting a value nobody vouched for.
 */
export function callerIp(request: Request): string {
  return callerIpFromHeaders(request.headers);
}

/** {@link callerIp} for a server component, which has headers but no Request. */
export function callerIpFromHeaders(headers: Pick<Headers, "get">): string {
  const forwarded = headers.get("x-forwarded-for") || "";
  const first = forwarded.split(",")[0]?.trim() || "";
  if (first) return first;
  return (headers.get("x-real-ip") || "").trim();
}
