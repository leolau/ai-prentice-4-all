/**
 * Service-to-service auth for the app-channel push seams (server-only).
 * The Python sender presents the shared secret as `x-app-push-secret`.
 */
import "server-only";

import { timingSafeEqual } from "node:crypto";

import { appPushSecret } from "@/lib/env";

export const APP_PUSH_SECRET_HEADER = "x-app-push-secret";

export function verifyAppPushSecret(request: Request): boolean {
  const expected = appPushSecret();
  if (!expected) return false;
  const presented = request.headers.get(APP_PUSH_SECRET_HEADER) ?? "";
  const a = Buffer.from(expected);
  const b = Buffer.from(presented);
  return a.byteLength === b.byteLength && timingSafeEqual(a, b);
}
