/**
 * VAPID keypair generation for the app channel's Web Push (server-only).
 *
 * The browser's `pushManager.subscribe` needs the public key as a base64url
 * uncompressed P-256 point (RFC 8292); pywebpush on the sending side needs
 * the private key as PKCS#8 PEM. Both come from one `generateKeyPairSync`
 * on the P-256 curve.
 */
import "server-only";

import { generateKeyPairSync } from "node:crypto";

export interface VapidDocument {
  /**
   * base64url PKCS#8 DER — what py_vapid's `from_string` parses (RAW or
   * DER; it rejects PEM with an ASN.1 error), consumed by pywebpush's
   * `vapid_private_key`.
   */
  private_key: string;
  /** base64url uncompressed P-256 point — the browser's applicationServerKey. */
  public_key: string;
  created_at: string;
}

export function generateVapidKeypair(): VapidDocument {
  const { privateKey, publicKey } = generateKeyPairSync("ec", {
    namedCurve: "prime256v1",
  });
  const der = privateKey.export({ type: "pkcs8", format: "der" });
  // JWK carries the affine coordinates as fixed-width base64url octets.
  const jwk = publicKey.export({ format: "jwk" });
  if (!jwk.x || !jwk.y) {
    throw new Error("agent-home: VAPID key export missing coordinates");
  }
  const x = Buffer.from(jwk.x, "base64url");
  const y = Buffer.from(jwk.y, "base64url");
  const raw = Buffer.concat([Buffer.from([0x04]), x, y]);
  return {
    private_key: Buffer.from(der).toString("base64url"),
    public_key: raw.toString("base64url"),
    created_at: new Date().toISOString(),
  };
}
